# ----------------------------------------------------------------------
# Numenta Platform for Intelligent Computing (NuPIC)
# Copyright (C) 2013-2014, Numenta, Inc.  Unless you have an agreement
# with Numenta, Inc., for a separate license for this software code, the
# following terms and conditions apply:
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 3 as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see http://www.gnu.org/licenses.
#
# http://numenta.org/licenses/
# ----------------------------------------------------------------------

import itertools
import numbers

import numpy
from nupic.bindings.math import (SM32 as SparseMatrix,
                                 SM_01_32_32 as SparseBinaryMatrix,
                                 GetNTAReal,
                                 Random as NupicRandom)


realDType = GetNTAReal()
uintType = "uint32"

VERSION = 2


class SpatialPooler(object):
  """
  This class implements the spatial pooler. It is in charge of handling the
  relationships between the columns of a region and the inputs bits. The
  primary public interface to this function is the "compute" method, which
  takes in an input vector and returns a list of activeColumns columns.
  Example Usage:
  >
  > sp = SpatialPooler(...)
  > for line in file:
  >   inputVector = numpy.array(line)
  >   sp.compute(inputVector)
  >   ...
  """

  def __init__(self,
               inputDimensions=(32,32),
               columnDimensions=(64,64),
               potentialRadius=16,
               potentialPct=0.5,
               globalInhibition=False,
               localAreaDensity=-1.0,
               numActiveColumnsPerInhArea=10.0,
               stimulusThreshold=0,
               synPermInactiveDec=0.008,
               synPermActiveInc=0.05,
               synPermConnected=0.10,
               minPctOverlapDutyCycles=0.001,
               minPctActiveDutyCycles=0.001,
               dutyCyclePeriod=1000,
               maxBoost=10.0,
               seed=-1,
               spVerbosity=0,
               wrapAround=True
               ):
    """
    Parameters:
    ----------------------------
    @param inputDimensions:
      A list representing the dimensions of the input vector. Format is [height,
      width, depth, ...], where each value represents the size of the dimension.
      For a topology of one dimension with 100 inputs use 100, or [100]. For a
      two dimensional topology of 10x5 use [10,5].
    @param columnDimensions:
      A list representing the dimensions of the columns in the region. Format is
      [height, width, depth, ...], where each value represents the size of the
      dimension.  For a topology of one dimension with 2000 columns use 2000, or
      [2000]. For a three dimensional topology of 32x64x16 use [32, 64, 16].
    @param potentialRadius:
      This parameter determines the extent of the input that each column can
      potentially be connected to.  This can be thought of as the input bits
      that are visible to each column, or a 'receptiveField' of the field of
      vision. A large enough value will result in 'global coverage', meaning
      that each column can potentially be connected to every input bit. This
      parameter defines a square (or hyper
      square) area: a column will have a max square potential pool with sides of
      length 2 * potentialRadius + 1.
    @param potentialPct:
      The percent of the inputs, within a column's potential radius, that a
      column can be connected to.  If set to 1, the column will be connected
      to every input within its potential radius. This parameter is used to
      give each column a unique potential pool when a large potentialRadius
      causes overlap between the columns. At initialization time we choose
      ((2*potentialRadius + 1)^(# inputDimensions) * potentialPct) input bits
      to comprise the column's potential pool.
    @param globalInhibition:
      If true, then during inhibition phase the winning columns are selected
      as the most active columns from the region as a whole. Otherwise, the
      winning columns are selected with respect to their local neighborhoods.
      Using global inhibition boosts performance x60.
    @param localAreaDensity:
      The desired density of active columns within a local inhibition area
      (the size of which is set by the internally calculated inhibitionRadius,
      which is in turn determined from the average size of the connected
      potential pools of all columns). The inhibition logic will insure that
      at most N columns remain ON within a local inhibition area, where
      N = localAreaDensity * (total number of columns in inhibition area).
    @param numActiveColumnsPerInhArea:
      An alternate way to control the density of the active columns. If
      numActiveColumnsPerInhArea is specified then localAreaDensity must be less than
      0, and vice versa.  When using numActiveColumnsPerInhArea, the inhibition logic
      will insure that at most 'numActiveColumnsPerInhArea' columns remain ON within a
      local inhibition area (the size of which is set by the internally
      calculated inhibitionRadius, which is in turn determined from the average
      size of the connected receptive fields of all columns). When using this
      method, as columns learn and grow their effective receptive fields, the
      inhibitionRadius will grow, and hence the net density of the active
      columns will *decrease*. This is in contrast to the localAreaDensity
      method, which keeps the density of active columns the same regardless of
      the size of their receptive fields.
    @param stimulusThreshold:
      This is a number specifying the minimum number of synapses that must be on
      in order for a columns to turn ON. The purpose of this is to prevent noise
      input from activating columns. Specified as a percent of a fully grown
      synapse.
    @param synPermInactiveDec:
      The amount by which an inactive synapse is decremented in each round.
      Specified as a percent of a fully grown synapse.
    @param synPermActiveInc:
      The amount by which an active synapse is incremented in each round.
      Specified as a percent of a fully grown synapse.
    @param synPermConnected:
      The default connected threshold. Any synapse whose permanence value is
      above the connected threshold is a "connected synapse", meaning it can
      contribute to the cell's firing.
    @param minPctOverlapDutyCycles:
      A number between 0 and 1.0, used to set a floor on how often a column
      should have at least stimulusThreshold active inputs. Periodically, each
      column looks at the overlap duty cycle of all other columns within its
      inhibition radius and sets its own internal minimal acceptable duty cycle
      to: minPctDutyCycleBeforeInh * max(other columns' duty cycles).  On each
      iteration, any column whose overlap duty cycle falls below this computed
      value will  get all of its permanence values boosted up by
      synPermActiveInc. Raising all permanences in response to a sub-par duty
      cycle before  inhibition allows a cell to search for new inputs when
      either its previously learned inputs are no longer ever active, or when
      the vast majority of them have been "hijacked" by other columns.
    @param minPctActiveDutyCycles:
      A number between 0 and 1.0, used to set a floor on how often a column
      should be activate.  Periodically, each column looks at the activity duty
      cycle of all other columns within its inhibition radius and sets its own
      internal minimal acceptable duty cycle to: minPctDutyCycleAfterInh *
      max(other columns' duty cycles).  On each iteration, any column whose duty
      cycle after inhibition falls below this computed value will get its
      internal boost factor increased.
    @param dutyCyclePeriod:
      The period used to calculate duty cycles. Higher values make it take
      longer to respond to changes in boost or synPerConnectedCell. Shorter
      values make it more unstable and likely to oscillate.
    @param maxBoost:
      The maximum overlap boost factor. Each column's overlap gets multiplied
      by a boost factor before it gets considered for inhibition.  The actual
      boost factor for a column is number between 1.0 and maxBoost. A boost
      factor of 1.0 is used if the duty cycle is >= minOverlapDutyCycle,
      maxBoost is used if the duty cycle is 0, and any duty cycle in between is
      linearly extrapolated from these 2 endpoints.
    @param seed:
      Seed for our own pseudo-random number generator.
    @param spVerbosity:
      spVerbosity level: 0, 1, 2, or 3
    @param wrapAround:
      Determines if inputs at the beginning and end of an input dimension should
      be considered neighbors when mapping columns to inputs.
    """
    # Verify input is valid
    inputDimensions = numpy.array(inputDimensions, ndmin=1)
    columnDimensions = numpy.array(columnDimensions, ndmin=1)
    numColumns = columnDimensions.prod()
    numInputs = inputDimensions.prod()

    assert numColumns > 0, "No columns specified"
    assert numInputs > 0, "No inputs specified"
    assert inputDimensions.size == columnDimensions.size, (
             "Input dimensions must match column dimensions")
    assert (numActiveColumnsPerInhArea > 0 or
           (localAreaDensity > 0 and localAreaDensity <= 0.5)), (
             "Inhibition parameters are invalid")

    # save arguments
    self._numInputs = int(numInputs)
    self._numColumns = int(numColumns)
    self.columnDimensions = columnDimensions
    self.inputDimensions = inputDimensions
    self.potentialRadius = int(min(potentialRadius, numInputs))
    self.potentialPct = potentialPct
    self.globalInhibition = globalInhibition
    self.numActiveColumnsPerInhArea = int(numActiveColumnsPerInhArea)
    self.localAreaDensity = localAreaDensity
    self.stimulusThreshold = stimulusThreshold
    self.synPermInactiveDec = synPermInactiveDec
    self.synPermActiveInc = synPermActiveInc
    self._synPermBelowStimulusInc = synPermConnected / 10.0
    self.synPermConnected = synPermConnected
    self.minPctOverlapDutyCycles = minPctOverlapDutyCycles
    self.minPctActiveDutyCycles = minPctActiveDutyCycles
    self.dutyCyclePeriod = dutyCyclePeriod
    self.maxBoost = maxBoost
    self.seed = seed
    self.spVerbosity = spVerbosity
    self.wrapAround = wrapAround

    # Extra parameter settings
    self._synPermMin = 0.0
    self._synPermMax = 1.0
    self._synPermTrimThreshold = synPermActiveInc / 2.0
    assert (self._synPermTrimThreshold < self.synPermConnected), (
             "synPermTrimThreshold must be less than synPermConnected")
    self._updatePeriod = 50
    initConnectedPct = 0.5

    # Internal state
    self._version = VERSION
    self._iterationNum = 0
    self._iterationLearnNum = 0

    # initialize the random number generators
    self.setRandomSeed(seed)

    # Store the set of all inputs that are within each column's potential pool.
    # 'potentialPools' is a matrix, whose rows represent cortical columns, and
    # whose columns represent the input bits. if potentialPools[i][j] == 1,
    # then input bit 'j' is in column 'i's potential pool. A column can only be
    # connected to inputs in its potential pool. The indices refer to a
    # flattened version of both the inputs and columns. Namely, irrespective
    # of the topology of the inputs and columns, they are treated as being a
    # one dimensional array. Since a column is typically connected to only a
    # subset of the inputs, many of the entries in the matrix are 0. Therefore
    # the potentialPool matrix is stored using the SparseBinaryMatrix
    # class, to reduce memory footprint and computation time of algorithms that
    # require iterating over the data structure.
    self._potentialPools = SparseBinaryMatrix(numInputs)
    self._potentialPools.resize(numColumns, numInputs)

    # Initialize the permanences for each column. Similar to the
    # 'self._potentialPools', the permanences are stored in a matrix whose rows
    # represent the cortical columns, and whose columns represent the input
    # bits. If self._permanences[i][j] = 0.2, then the synapse connecting
    # cortical column 'i' to input bit 'j'  has a permanence of 0.2. Here we
    # also use the SparseMatrix class to reduce the memory footprint and
    # computation time of algorithms that require iterating over the data
    # structure. This permanence matrix is only allowed to have non-zero
    # elements where the potential pool is non-zero.
    self._permanences = SparseMatrix(numColumns, numInputs)

    # Initialize a tiny random tie breaker. This is used to determine winning
    # columns where the overlaps are identical.
    self._tieBreaker = 0.01*numpy.array([self._random.getReal64() for i in
                                        xrange(self._numColumns)])


    # 'self._connectedSynapses' is a similar matrix to 'self._permanences'
    # (rows represent cortical columns, columns represent input bits) whose
    # entries represent whether the cortical column is connected to the input
    # bit, i.e. its permanence value is greater than 'synPermConnected'. While
    # this information is readily available from the 'self._permanence' matrix,
    # it is stored separately for efficiency purposes.
    self._connectedSynapses = SparseBinaryMatrix(numInputs)
    self._connectedSynapses.resize(numColumns, numInputs)

    # Stores the number of connected synapses for each column. This is simply
    # a sum of each row of 'self._connectedSynapses'. again, while this
    # information is readily available from 'self._connectedSynapses', it is
    # stored separately for efficiency purposes.
    self._connectedCounts = numpy.zeros(numColumns, dtype=realDType)

    # Initialize the set of permanence values for each column. Ensure that
    # each column is connected to enough input bits to allow it to be
    # activated.
    for i in xrange(numColumns):
      potential = self._mapPotential(i, wrapAround=self.wrapAround)
      self._potentialPools.replaceSparseRow(i, potential.nonzero()[0])
      perm = self._initPermanence(potential, initConnectedPct)
      self._updatePermanencesForColumn(perm, i, raisePerm=True)


    self._overlapDutyCycles = numpy.zeros(numColumns, dtype=realDType)
    self._activeDutyCycles = numpy.zeros(numColumns, dtype=realDType)
    self._minOverlapDutyCycles = numpy.zeros(numColumns,
                                             dtype=realDType)
    self._minActiveDutyCycles = numpy.zeros(numColumns,
                                            dtype=realDType)
    self._boostFactors = numpy.ones(numColumns, dtype=realDType)

    # The inhibition radius determines the size of a column's local
    # neighborhood. of a column. A cortical column must overcome the overlap
    # score of columns in his neighborhood in order to become actives. This
    # radius is updated every learning round. It grows and shrinks with the
    # average number of connected synapses per column.
    self._inhibitionRadius = 0
    self._updateInhibitionRadius()

    if self.spVerbosity > 0:
      self.printParameters()


  def __eq__(self, other):
    print "sp"
    for k, v1 in self.__dict__.iteritems():
      if not k in other.__dict__:
        print 'not found: ', k
        return False
      v2 = getattr(other, k)
      if isinstance(v1, NupicRandom) or isinstance(v1, SparseBinaryMatrix):
        pass
      elif isinstance(v1, numpy.ndarray):
        if v1.dtype != v2.dtype:
          print v1, v2, k, 'v1.dtype != v2.dtype'
          return False
        if not numpy.isclose(v1, v2).all():
          print v1, v2, k, 'not numpy.isclose(v1, v2).all()'
          return False
      elif isinstance(v1, float):
        if abs(v1 - v2) > 0.00000001:
          print v1, v2, k, 'abs(v1 - v2) > 0.00000001'
          return False
      elif isinstance(v1, numbers.Integral):
        if long(v1) != long(v2):
          print v1, v2, k, 'long(v1) != long(v2)'
          return False
      else:
        if type(v1) != type(v2):
          print v1, v2, k, 'type(v1) != type(v2)'
          return False
        if v1 != v2:
          print v1, v2, k, 'v1 != v2'
          return False
    return True


  def __ne__(self, other):
    return not self == other


  def getColumnDimensions(self):
    """Returns the dimensions of the columns in the region"""
    return self.columnDimensions


  def getInputDimensions(self):
    """Returns the dimensions of the input vector"""
    return self.inputDimensions


  def getNumColumns(self):
    """Returns the total number of columns"""
    return self._numColumns


  def getNumInputs(self):
    """Returns the total number of inputs"""
    return self._numInputs


  def getPotentialRadius(self):
    """Returns the potential radius"""
    return self.potentialRadius


  def setPotentialRadius(self, potentialRadius):
    """Sets the potential radius"""
    self.potentialRadius = potentialRadius


  def getPotentialPct(self):
    """Returns the potential percent"""
    return self.potentialPct


  def setPotentialPct(self, potentialPct):
    """Sets the potential percent"""
    self.potentialPct = potentialPct


  def getGlobalInhibition(self):
    """Returns whether global inhibition is enabled"""
    return self.globalInhibition


  def setGlobalInhibition(self, globalInhibition):
    """Sets global inhibition"""
    self.globalInhibition = globalInhibition


  def getNumActiveColumnsPerInhArea(self):
    """Returns the number of active columns per inhibition area. Returns a
    value less than 0 if parameter is unused"""
    return self.numActiveColumnsPerInhArea


  def setNumActiveColumnsPerInhArea(self, numActiveColumnsPerInhArea):
    """Sets the number of active columns per inhibition area. Invalidates the
    'localAreaDensity' parameter"""
    assert(numActiveColumnsPerInhArea > 0)
    self.numActiveColumnsPerInhArea = numActiveColumnsPerInhArea
    self.localAreaDensity = 0


  def getLocalAreaDensity(self):
    """Returns the local area density. Returns a value less than 0 if parameter
    is unused"""
    return self.localAreaDensity


  def setLocalAreaDensity(self, localAreaDensity):
    """Sets the local area density. Invalidates the 'numActiveColumnsPerInhArea'
    parameter"""
    assert(localAreaDensity > 0 and localAreaDensity <= 1)
    self.localAreaDensity = localAreaDensity
    self.numActiveColumnsPerInhArea = 0


  def getStimulusThreshold(self):
    """Returns the stimulus threshold"""
    return self.stimulusThreshold


  def setStimulusThreshold(self, stimulusThreshold):
    """Sets the stimulus threshold"""
    self.stimulusThreshold = stimulusThreshold


  def getInhibitionRadius(self):
    """Returns the inhibition radius"""
    return self._inhibitionRadius


  def setInhibitionRadius(self, inhibitionRadius):
    """Sets the inhibition radius"""
    self._inhibitionRadius = inhibitionRadius


  def getDutyCyclePeriod(self):
    """Returns the duty cycle period"""
    return self.dutyCyclePeriod


  def setDutyCyclePeriod(self, dutyCyclePeriod):
    """Sets the duty cycle period"""
    self.dutyCyclePeriod = dutyCyclePeriod


  def getMaxBoost(self):
    """Returns the maximum boost value"""
    return self.maxBoost


  def setMaxBoost(self, maxBoost):
    """Sets the maximum boost value"""
    self.maxBoost = maxBoost


  def getIterationNum(self):
    """Returns the iteration number"""
    return self._iterationNum


  def setIterationNum(self, iterationNum):
    """Sets the iteration number"""
    self._iterationNum = iterationNum


  def getIterationLearnNum(self):
    """Returns the learning iteration number"""
    return self._iterationLearnNum


  def setIterationLearnNum(self, iterationLearnNum):
    """Sets the learning iteration number"""
    self._iterationLearnNum = iterationLearnNum


  def getSpVerbosity(self):
    """Returns the verbosity level"""
    return self.spVerbosity


  def setSpVerbosity(self, spVerbosity):
    """Sets the verbosity level"""
    self.spVerbosity = spVerbosity


  def getUpdatePeriod(self):
    """Returns the update period"""
    return self._updatePeriod


  def setUpdatePeriod(self, updatePeriod):
    """Sets the update period"""
    self._updatePeriod = updatePeriod


  def getSynPermTrimThreshold(self):
    """Returns the permanence trim threshold"""
    return self._synPermTrimThreshold


  def setSynPermTrimThreshold(self, synPermTrimThreshold):
    """Sets the permanence trim threshold"""
    self._synPermTrimThreshold = synPermTrimThreshold


  def getSynPermActiveInc(self):
    """Returns the permanence increment amount for active synapses
    inputs"""
    return self.synPermActiveInc


  def setSynPermActiveInc(self, synPermActiveInc):
    """Sets the permanence increment amount for active synapses"""
    self.synPermActiveInc = synPermActiveInc


  def getSynPermInactiveDec(self):
    """Returns the permanence decrement amount for inactive synapses"""
    return self.synPermInactiveDec


  def setSynPermInactiveDec(self, synPermInactiveDec):
    """Sets the permanence decrement amount for inactive synapses"""
    self.synPermInactiveDec = synPermInactiveDec


  def getSynPermBelowStimulusInc(self):
    """Returns the permanence increment amount for columns that have not been
    recently active """
    return self._synPermBelowStimulusInc


  def setSynPermBelowStimulusInc(self, synPermBelowStimulusInc):
    """Sets the permanence increment amount for columns that have not been
    recently active """
    self._synPermBelowStimulusInc = synPermBelowStimulusInc


  def getSynPermConnected(self):
    """Returns the permanence amount that qualifies a synapse as
    being connected"""
    return self.synPermConnected


  def setSynPermConnected(self, synPermConnected):
    """Sets the permanence amount that qualifies a synapse as being
    connected"""
    self.synPermConnected = synPermConnected


  def getMinPctOverlapDutyCycles(self):
    """Returns the minimum tolerated overlaps, given as percent of
    neighbors overlap score"""
    return self.minPctOverlapDutyCycles


  def setMinPctOverlapDutyCycles(self, minPctOverlapDutyCycles):
    """Sets the minimum tolerated activity duty cycle, given as percent of
    neighbors' activity duty cycle"""
    self.minPctOverlapDutyCycles = minPctOverlapDutyCycles


  def getMinPctActiveDutyCycles(self):
    """Returns the minimum tolerated activity duty cycle, given as percent of
    neighbors' activity duty cycle"""
    return self.minPctActiveDutyCycles


  def setMinPctActiveDutyCycles(self, minPctActiveDutyCycles):
    """Sets the minimum tolerated activity duty, given as percent of
    neighbors' activity duty cycle"""
    self.minPctActiveDutyCycles = minPctActiveDutyCycles


  def getBoostFactors(self, boostFactors):
    """Returns the boost factors for all columns. 'boostFactors' size must
    match the number of columns"""
    boostFactors[:] = self._boostFactors[:]


  def setBoostFactors(self, boostFactors):
    """Sets the boost factors for all columns. 'boostFactors' size must match
    the number of columns"""
    self._boostFactors[:] = boostFactors[:]


  def getOverlapDutyCycles(self, overlapDutyCycles):
    """Returns the overlap duty cycles for all columns. 'overlapDutyCycles'
    size must match the number of columns"""
    overlapDutyCycles[:] = self._overlapDutyCycles[:]


  def setOverlapDutyCycles(self, overlapDutyCycles):
    """Sets the overlap duty cycles for all columns. 'overlapDutyCycles'
    size must match the number of columns"""
    self._overlapDutyCycles[:] = overlapDutyCycles


  def getActiveDutyCycles(self, activeDutyCycles):
    """Returns the activity duty cycles for all columns. 'activeDutyCycles'
    size must match the number of columns"""
    activeDutyCycles[:] = self._activeDutyCycles[:]


  def setActiveDutyCycles(self, activeDutyCycles):
    """Sets the activity duty cycles for all columns. 'activeDutyCycles'
    size must match the number of columns"""
    self._activeDutyCycles[:] = activeDutyCycles


  def getMinOverlapDutyCycles(self, minOverlapDutyCycles):
    """Returns the minimum overlap duty cycles for all columns.
    '_minOverlapDutyCycles' size must match the number of columns"""
    minOverlapDutyCycles[:] = self._minOverlapDutyCycles[:]


  def setMinOverlapDutyCycles(self, minOverlapDutyCycles):
    """Sets the minimum overlap duty cycles for all columns.
    '_minOverlapDutyCycles' size must match the number of columns"""
    self._minOverlapDutyCycles[:] = minOverlapDutyCycles[:]


  def getMinActiveDutyCycles(self, minActiveDutyCycles):
    """Returns the minimum activity duty cycles for all columns.
    '_minActiveDutyCycles' size must match the number of columns"""
    minActiveDutyCycles[:] = self._minActiveDutyCycles[:]


  def setMinActiveDutyCycles(self, minActiveDutyCycles):
    """Sets the minimum activity duty cycles for all columns.
    '_minActiveDutyCycles' size must match the number of columns"""
    self._minActiveDutyCycles = minActiveDutyCycles


  def getPotential(self, column, potential):
    """Returns the potential mapping for a given column. 'potential' size
    must match the number of inputs"""
    assert(column < self._numColumns)
    potential[:] = self._potentialPools.getRow(column)


  def setPotential(self, column, potential):
    """Sets the potential mapping for a given column. 'potential' size
    must match the number of inputs, and must be greater than stimulusThreshold """
    assert(column < self._numColumns)

    potentialSparse = numpy.where(potential > 0)[0]
    if len(potentialSparse) < self.stimulusThreshold:
      raise Exception("This is likely due to a " +
      "value of stimulusThreshold that is too large relative " +
      "to the input size.")

    self._potentialPools.replaceSparseRow(column, potentialSparse)


  def getPermanence(self, column, permanence):
    """Returns the permanence values for a given column. 'permanence' size
    must match the number of inputs"""
    assert(column < self._numColumns)
    permanence[:] = self._permanences.getRow(column)


  def setPermanence(self, column, permanence):
    """Sets the permanence values for a given column. 'permanence' size
    must match the number of inputs"""
    assert(column < self._numColumns)
    self._updatePermanencesForColumn(permanence, column, raisePerm=False)


  def getConnectedSynapses(self, column, connectedSynapses):
    """Returns the connected synapses for a given column.
    'connectedSynapses' size must match the number of inputs"""
    assert(column < self._numColumns)
    connectedSynapses[:] = self._connectedSynapses.getRow(column)


  def getConnectedCounts(self, connectedCounts):
    """Returns the number of connected synapses for all columns.
    'connectedCounts' size must match the number of columns"""
    connectedCounts[:] = self._connectedCounts[:]


  def compute(self, inputVector, learn, activeArray, stripNeverLearned=True):
    """
    This is the primary public method of the SpatialPooler class. This
    function takes a input vector and outputs the indices of the active columns.
    If 'learn' is set to True, this method also updates the permanences of the
    columns.

    @param inputVector: A numpy array of 0's and 1's that comprises the input
        to the spatial pooler. The array will be treated as a one dimensional
        array, therefore the dimensions of the array do not have to match the
        exact dimensions specified in the class constructor. In fact, even a
        list would suffice. The number of input bits in the vector must,
        however, match the number of bits specified by the call to the
        constructor. Therefore there must be a '0' or '1' in the array for
        every input bit.
    @param learn: A boolean value indicating whether learning should be
        performed. Learning entails updating the  permanence values of the
        synapses, and hence modifying the 'state' of the model. Setting
        learning to 'off' freezes the SP and has many uses. For example, you
        might want to feed in various inputs and examine the resulting SDR's.
    @param activeArray: An array whose size is equal to the number of columns.
        Before the function returns this array will be populated with 1's at
        the indices of the active columns, and 0's everywhere else.
    @param stripNeverLearned: If True and learn=False, then columns that
        have never learned will be stripped out of the active columns. This
        should be set to False when using a random SP with learning disabled.
        NOTE: This parameter should be set explicitly as the default will
        likely be changed to False in the near future and if you want to retain
        the current behavior you should additionally pass the resulting
        activeArray to the stripUnlearnedColumns method manually.
    """
    if not isinstance(inputVector, numpy.ndarray):
      raise TypeError("Input vector must be a numpy array, not %s" %
                      str(type(inputVector)))

    if inputVector.size != self._numInputs:
      raise ValueError(
          "Input vector dimensions don't match. Expecting %s but got %s" % (
              inputVector.size, self._numInputs))

    self._updateBookeepingVars(learn)
    inputVector = numpy.array(inputVector, dtype=realDType)
    inputVector.reshape(-1)
    overlaps = self._calculateOverlap(inputVector)

    # Apply boosting when learning is on
    if learn:
      boostedOverlaps = self._boostFactors * overlaps
    else:
      boostedOverlaps = overlaps

    # Apply inhibition to determine the winning columns
    activeColumns = self._inhibitColumns(boostedOverlaps)

    if learn:
      self._adaptSynapses(inputVector, activeColumns)
      self._updateDutyCycles(overlaps, activeColumns)
      self._bumpUpWeakColumns()
      self._updateBoostFactors()
      if self._isUpdateRound():
        self._updateInhibitionRadius()
        self._updateMinDutyCycles()
    elif stripNeverLearned:
      activeColumns = self.stripUnlearnedColumns(activeColumns)

    activeArray.fill(0)
    if activeColumns.size > 0:
      activeArray[activeColumns] = 1


  def stripUnlearnedColumns(self, activeColumns):
    """Removes the set of columns who have never been active from the set of
    active columns selected in the inhibition round. Such columns cannot
    represent learned pattern and are therefore meaningless if only inference
    is required. This should not be done when using a random, unlearned SP
    since you would end up with no active columns.

    @param activeColumns: An array containing the indices of the active columns
    """
    neverLearned = numpy.where(self._activeDutyCycles == 0)[0]
    return numpy.array(list(set(activeColumns) - set(neverLearned)))


  def _updateMinDutyCycles(self):
    """
    Updates the minimum duty cycles defining normal activity for a column. A
    column with activity duty cycle below this minimum threshold is boosted.
    """
    if self.globalInhibition or self._inhibitionRadius > self._numInputs:
      self._updateMinDutyCyclesGlobal()
    else:
      self._updateMinDutyCyclesLocal()


  def _updateMinDutyCyclesGlobal(self):
    """
    Updates the minimum duty cycles in a global fashion. Sets the minimum duty
    cycles for the overlap and activation of all columns to be a percent of the
    maximum in the region, specified by minPctOverlapDutyCycles and
    minPctActiveDutyCycles respectively. Functionality it is equivalent to
    _updateMinDutyCyclesLocal, but this function exploits the globality of the
    computation to perform it in a straightforward, and more efficient manner.
    """
    self._minOverlapDutyCycles.fill(
        self.minPctOverlapDutyCycles * self._overlapDutyCycles.max()
      )
    self._minActiveDutyCycles.fill(
        self.minPctActiveDutyCycles * self._activeDutyCycles.max()
      )


  def _updateMinDutyCyclesLocal(self):
    """
    Updates the minimum duty cycles. The minimum duty cycles are determined
    locally. Each column's minimum duty cycles are set to be a percent of the
    maximum duty cycles in the column's neighborhood. Unlike
    _updateMinDutyCyclesGlobal, here the values can be quite different for
    different columns.
    """
    for i in xrange(self._numColumns):
      maskNeighbors = numpy.append(i,
        self._getNeighborsND(i, self.columnDimensions,
        self._inhibitionRadius))
      self._minOverlapDutyCycles[i] = (
        self._overlapDutyCycles[maskNeighbors].max() *
        self.minPctOverlapDutyCycles
      )
      self._minActiveDutyCycles[i] = (
        self._activeDutyCycles[maskNeighbors].max() *
        self.minPctActiveDutyCycles
      )


  def _updateDutyCycles(self, overlaps, activeColumns):
    """
    Updates the duty cycles for each column. The OVERLAP duty cycle is a moving
    average of the number of inputs which overlapped with the each column. The
    ACTIVITY duty cycles is a moving average of the frequency of activation for
    each column.

    Parameters:
    ----------------------------
    @param overlaps:
                    An array containing the overlap score for each column.
                    The overlap score for a column is defined as the number
                    of synapses in a "connected state" (connected synapses)
                    that are connected to input bits which are turned on.
    @param activeColumns:
                    An array containing the indices of the active columns,
                    the sparse set of columns which survived inhibition
    """
    overlapArray = numpy.zeros(self._numColumns, dtype=realDType)
    activeArray = numpy.zeros(self._numColumns, dtype=realDType)
    overlapArray[overlaps > 0] = 1
    if activeColumns.size > 0:
      activeArray[activeColumns] = 1

    period = self.dutyCyclePeriod
    if (period > self._iterationNum):
      period = self._iterationNum

    self._overlapDutyCycles = self._updateDutyCyclesHelper(
                                self._overlapDutyCycles,
                                overlapArray,
                                period
                              )

    self._activeDutyCycles = self._updateDutyCyclesHelper(
                                self._activeDutyCycles,
                                activeArray,
                                period
                              )



  def _updateInhibitionRadius(self):
    """
    Update the inhibition radius. The inhibition radius is a measure of the
    square (or hypersquare) of columns that each a column is "connected to"
    on average. Since columns are are not connected to each other directly, we
    determine this quantity by first figuring out how many *inputs* a column is
    connected to, and then multiplying it by the total number of columns that
    exist for each input. For multiple dimension the aforementioned
    calculations are averaged over all dimensions of inputs and columns. This
    value is meaningless if global inhibition is enabled.
    """
    if self.globalInhibition:
      self._inhibitionRadius = self.columnDimensions.max()
      return

    avgConnectedSpan = numpy.average(
                          [self._avgConnectedSpanForColumnND(i)
                          for i in xrange(self._numColumns)]
                        )
    columnsPerInput = self._avgColumnsPerInput()
    diameter = avgConnectedSpan * columnsPerInput
    radius = (diameter - 1) / 2.0
    radius = max(1.0, radius)
    self._inhibitionRadius = int(round(radius))


  def _avgColumnsPerInput(self):
    """
    The average number of columns per input, taking into account the topology
    of the inputs and columns. This value is used to calculate the inhibition
    radius. This function supports an arbitrary number of dimensions. If the
    number of column dimensions does not match the number of input dimensions,
    we treat the missing, or phantom dimensions as 'ones'.
    """
    #TODO: extend to support different number of dimensions for inputs and
    # columns
    numDim = max(self.columnDimensions.size, self.inputDimensions.size)
    colDim = numpy.ones(numDim)
    colDim[:self.columnDimensions.size] = self.columnDimensions

    inputDim = numpy.ones(numDim)
    inputDim[:self.inputDimensions.size] = self.inputDimensions

    columnsPerInput = colDim.astype(realDType) / inputDim
    return numpy.average(columnsPerInput)


  def _avgConnectedSpanForColumn1D(self, index):
    """
    The range of connected synapses for column. This is used to
    calculate the inhibition radius. This variation of the function only
    supports a 1 dimensional column topology.

    Parameters:
    ----------------------------
    @param index:   The index identifying a column in the permanence, potential
                    and connectivity matrices,
    """
    assert(self.inputDimensions.size == 1)
    connected = self._connectedSynapses.getRow(index).nonzero()[0]
    if connected.size == 0:
      return 0
    else:
      return max(connected) - min(connected) + 1


  def _avgConnectedSpanForColumn2D(self, index):
    """
    The range of connectedSynapses per column, averaged for each dimension.
    This value is used to calculate the inhibition radius. This variation of
    the  function only supports a 2 dimensional column topology.

    Parameters:
    ----------------------------
    @param index:   The index identifying a column in the permanence, potential
                    and connectivity matrices,
    """
    assert(self.inputDimensions.size == 2)
    connected = self._connectedSynapses.getRow(index)
    (rows, cols) = connected.reshape(self.inputDimensions).nonzero()
    if  rows.size == 0 and cols.size == 0:
      return 0
    rowSpan = rows.max() - rows.min() + 1
    colSpan = cols.max() - cols.min() + 1
    return numpy.average([rowSpan, colSpan])


  def _avgConnectedSpanForColumnND(self, index):
    """
    The range of connectedSynapses per column, averaged for each dimension.
    This value is used to calculate the inhibition radius. This variation of
    the function supports arbitrary column dimensions.

    Parameters:
    ----------------------------
    @param index:   The index identifying a column in the permanence, potential
                    and connectivity matrices.
    """
    dimensions = self.inputDimensions
    connected = self._connectedSynapses.getRow(index).nonzero()[0]
    if connected.size == 0:
      return 0
    maxCoord = numpy.empty(self.inputDimensions.size)
    minCoord = numpy.empty(self.inputDimensions.size)
    maxCoord.fill(-1)
    minCoord.fill(max(self.inputDimensions))
    for i in connected:
      maxCoord = numpy.maximum(maxCoord, numpy.unravel_index(i, dimensions))
      minCoord = numpy.minimum(minCoord, numpy.unravel_index(i, dimensions))
    return numpy.average(maxCoord - minCoord + 1)


  def _adaptSynapses(self, inputVector, activeColumns):
    """
    The primary method in charge of learning. Adapts the permanence values of
    the synapses based on the input vector, and the chosen columns after
    inhibition round. Permanence values are increased for synapses connected to
    input bits that are turned on, and decreased for synapses connected to
    inputs bits that are turned off.

    Parameters:
    ----------------------------
    @param inputVector:
                    A numpy array of 0's and 1's that comprises the input to
                    the spatial pooler. There exists an entry in the array
                    for every input bit.
    @param activeColumns:
                    An array containing the indices of the columns that
                    survived inhibition.
    """
    inputIndices = numpy.where(inputVector > 0)[0]
    permChanges = numpy.zeros(self._numInputs)
    permChanges.fill(-1 * self.synPermInactiveDec)
    permChanges[inputIndices] = self.synPermActiveInc
    for i in activeColumns:
      perm = self._permanences.getRow(i)
      maskPotential = numpy.where(self._potentialPools.getRow(i) > 0)[0]
      perm[maskPotential] += permChanges[maskPotential]
      self._updatePermanencesForColumn(perm, i, raisePerm=True)


  def _bumpUpWeakColumns(self):
    """
    This method increases the permanence values of synapses of columns whose
    activity level has been too low. Such columns are identified by having an
    overlap duty cycle that drops too much below those of their peers. The
    permanence values for such columns are increased.
    """
    weakColumns = numpy.where(self._overlapDutyCycles
                                < self._minOverlapDutyCycles)[0]
    for i in weakColumns:
      perm = self._permanences.getRow(i).astype(realDType)
      maskPotential = numpy.where(self._potentialPools.getRow(i) > 0)[0]
      perm[maskPotential] += self._synPermBelowStimulusInc
      self._updatePermanencesForColumn(perm, i, raisePerm=False)


  def _raisePermanenceToThreshold(self, perm, mask):
    """
    This method ensures that each column has enough connections to input bits
    to allow it to become active. Since a column must have at least
    'self.stimulusThreshold' overlaps in order to be considered during the
    inhibition phase, columns without such minimal number of connections, even
    if all the input bits they are connected to turn on, have no chance of
    obtaining the minimum threshold. For such columns, the permanence values
    are increased until the minimum number of connections are formed.


    Parameters:
    ----------------------------
    @param perm:    An array of permanence values for a column. The array is
                    "dense", i.e. it contains an entry for each input bit, even
                    if the permanence value is 0.
    @param mask:    the indices of the columns whose permanences need to be
                    raised.
    """
    if len(mask) < self.stimulusThreshold:
      raise Exception("This is likely due to a " +
      "value of stimulusThreshold that is too large relative " +
      "to the input size. [len(mask) < self.stimulusThreshold]")

    numpy.clip(perm, self._synPermMin, self._synPermMax, out=perm)
    while True:
      numConnected = numpy.nonzero(perm > self.synPermConnected)[0].size
      if numConnected >= self.stimulusThreshold:
        return
      perm[mask] += self._synPermBelowStimulusInc


  def _updatePermanencesForColumn(self, perm, index, raisePerm=True):
    """
    This method updates the permanence matrix with a column's new permanence
    values. The column is identified by its index, which reflects the row in
    the matrix, and the permanence is given in 'dense' form, i.e. a full
    array containing all the zeros as well as the non-zero values. It is in
    charge of implementing 'clipping' - ensuring that the permanence values are
    always between 0 and 1 - and 'trimming' - enforcing sparsity by zeroing out
    all permanence values below '_synPermTrimThreshold'. It also maintains
    the consistency between 'self._permanences' (the matrix storing the
    permanence values), 'self._connectedSynapses', (the matrix storing the bits
    each column is connected to), and 'self._connectedCounts' (an array storing
    the number of input bits each column is connected to). Every method wishing
    to modify the permanence matrix should do so through this method.

    Parameters:
    ----------------------------
    @param perm:    An array of permanence values for a column. The array is
                    "dense", i.e. it contains an entry for each input bit, even
                    if the permanence value is 0.
    @param index:   The index identifying a column in the permanence, potential
                    and connectivity matrices
    @param raisePerm: A boolean value indicating whether the permanence values
                    should be raised until a minimum number are synapses are in
                    a connected state. Should be set to 'false' when a direct
                    assignment is required.
    """

    maskPotential = numpy.where(self._potentialPools.getRow(index) > 0)[0]
    if raisePerm:
      self._raisePermanenceToThreshold(perm, maskPotential)
    perm[perm < self._synPermTrimThreshold] = 0
    numpy.clip(perm, self._synPermMin, self._synPermMax, out=perm)
    newConnected = numpy.where(perm >= self.synPermConnected)[0]
    self._permanences.setRowFromDense(index, perm)
    self._connectedSynapses.replaceSparseRow(index, newConnected)
    self._connectedCounts[index] = newConnected.size


  def _initPermConnected(self):
    """
    Returns a randomly generated permanence value for a synapses that is
    initialized in a connected state. The basic idea here is to initialize
    permanence values very close to synPermConnected so that a small number of
    learning steps could make it disconnected or connected.

    Note: experimentation was done a long time ago on the best way to initialize
    permanence values, but the history for this particular scheme has been lost.
    """
    p = self.synPermConnected + (
        self.synPermMax - self.synPermConnected)*self._random.getReal64()

    # Ensure we don't have too much unnecessary precision. A full 64 bits of
    # precision causes numerical stability issues across platforms and across
    # implementations
    p = int(p*100000) / 100000.0
    return p


  def _initPermNonConnected(self):
    """
    Returns a randomly generated permanence value for a synapses that is to be
    initialized in a non-connected state.
    """
    p = self.synPermConnected * self._random.getReal64()

    # Ensure we don't have too much unnecessary precision. A full 64 bits of
    # precision causes numerical stability issues across platforms and across
    # implementations
    p = int(p*100000) / 100000.0
    return p

  def _initPermanence(self, potential, connectedPct):
    """
    Initializes the permanences of a column. The method
    returns a 1-D array the size of the input, where each entry in the
    array represents the initial permanence value between the input bit
    at the particular index in the array, and the column represented by
    the 'index' parameter.

    Parameters:
    ----------------------------
    @param potential: A numpy array specifying the potential pool of the column.
                    Permanence values will only be generated for input bits
                    corresponding to indices for which the mask value is 1.
    @param connectedPct: A value between 0 or 1 governing the chance, for each
                         permanence, that the initial permanence value will
                         be a value that is considered connected.
    """
    # Determine which inputs bits will start out as connected
    # to the inputs. Initially a subset of the input bits in a
    # column's potential pool will be connected. This number is
    # given by the parameter "connectedPct"
    perm = numpy.zeros(self._numInputs)
    for i in xrange(self._numInputs):
      if (potential[i] < 1):
        continue

      if (self._random.getReal64() <= connectedPct):
        perm[i] = self._initPermConnected()
      else:
        perm[i] = self._initPermNonConnected()

    # Clip off low values. Since we use a sparse representation
    # to store the permanence values this helps reduce memory
    # requirements.
    perm[perm < self._synPermTrimThreshold] = 0

    return perm


  def _mapColumn(self, index):
    """
    Maps a column to its respective input index, keeping to the topology of
    the region. It takes the index of the column as an argument and determines
    what is the index of the flattened input vector that is to be the center of
    the column's potential pool. It distributes the columns over the inputs
    uniformly. The return value is an integer representing the index of the
    input bit. Examples of the expected output of this method:
    * If the topology is one dimensional, and the column index is 0, this
      method will return the input index 0. If the column index is 1, and there
      are 3 columns over 7 inputs, this method will return the input index 3.
    * If the topology is two dimensional, with column dimensions [3, 5] and
      input dimensions [7, 11], and the column index is 3, the method
      returns input index 8.

    Parameters:
    ----------------------------
    @param index:   The index identifying a column in the permanence, potential
                    and connectivity matrices.
    @param wrapAround: A boolean value indicating that boundaries should be
                    ignored.
    """
    columnCoords = numpy.unravel_index(index, self.columnDimensions)
    columnCoords = numpy.array(columnCoords, dtype=realDType)
    ratios = columnCoords / self.columnDimensions
    inputCoords = self.inputDimensions * ratios
    inputCoords += 0.5 * self.inputDimensions / self.columnDimensions
    inputCoords = inputCoords.astype(int)
    inputIndex = numpy.ravel_multi_index(inputCoords, self.inputDimensions)
    return inputIndex


  def _mapPotential(self, index, wrapAround=False):
    """
    Maps a column to its input bits. This method encapsulates the topology of
    the region. It takes the index of the column as an argument and determines
    what are the indices of the input vector that are located within the
    column's potential pool. The return value is a list containing the indices
    of the input bits. The current implementation of the base class only
    supports a 1 dimensional topology of columns with a 1 dimensional topology
    of inputs. To extend this class to support 2-D topology you will need to
    override this method. Examples of the expected output of this method:
    * If the potentialRadius is greater than or equal to the largest input
      dimension then each column connects to all of the inputs.
    * If the topology is one dimensional, the input space is divided up evenly
      among the columns and each column is centered over its share of the
      inputs.  If the potentialRadius is 5, then each column connects to the
      input it is centered above as well as the 5 inputs to the left of that
      input and the five inputs to the right of that input, wrapping around if
      wrapAround=True.
    * If the topology is two dimensional, the input space is again divided up
      evenly among the columns and each column is centered above its share of
      the inputs.  If the potentialRadius is 5, the column connects to a square
      that has 11 inputs on a side and is centered on the input that the column
      is centered above.

    Parameters:
    ----------------------------
    @param index:   The index identifying a column in the permanence, potential
                    and connectivity matrices.
    @param wrapAround: A boolean value indicating that boundaries should be
                    fignored.
    """
    index = self._mapColumn(index)
    indices = self._getNeighborsND(index,
                                   self.inputDimensions,
                                   self.potentialRadius,
                                   wrapAround=wrapAround)
    indices.append(index)
    indices = numpy.array(indices, dtype=uintType)

    # TODO: See https://github.com/numenta/nupic.core/issues/128
    indices.sort()

    # Select a subset of the receptive field to serve as the
    # the potential pool
    numPotential = int(round(indices.size * self.potentialPct))
    selectedIndices = numpy.empty(numPotential, dtype=uintType)
    self._random.sample(indices, selectedIndices)

    potential = numpy.zeros(self._numInputs, dtype=uintType)
    potential[selectedIndices] = 1

    return potential


  @staticmethod
  def _updateDutyCyclesHelper(dutyCycles, newInput, period):
    """
    Updates a duty cycle estimate with a new value. This is a helper
    function that is used to update several duty cycle variables in
    the Column class, such as: overlapDutyCucle, activeDutyCycle,
    minPctDutyCycleBeforeInh, minPctDutyCycleAfterInh, etc. returns
    the updated duty cycle. Duty cycles are updated according to the following
    formula:

                  (period - 1)*dutyCycle + newValue
      dutyCycle := ----------------------------------
                              period

    Parameters:
    ----------------------------
    @param dutyCycles: An array containing one or more duty cycle values that need
                    to be updated
    @param newInput: A new numerical value used to update the duty cycle
    @param period:  The period of the duty cycle
    """
    assert(period >= 1)
    return (dutyCycles * (period -1.0) + newInput) / period


  def _updateBoostFactors(self):
    r"""
    Update the boost factors for all columns. The boost factors are used to
    increase the overlap of inactive columns to improve their chances of
    becoming active. and hence encourage participation of more columns in the
    learning process. This is a line defined as: y = mx + b boost =
    (1-maxBoost)/minDuty * dutyCycle + maxFiringBoost. Intuitively this means
    that columns that have been active enough have a boost factor of 1, meaning
    their overlap is not boosted. Columns whose active duty cycle drops too much
    below that of their neighbors are boosted depending on how infrequently they
    have been active. The more infrequent, the more they are boosted. The exact
    boost factor is linearly interpolated between the points (dutyCycle:0,
    boost:maxFiringBoost) and (dutyCycle:minDuty, boost:1.0).

            boostFactor
                ^
    maxBoost _  |
                |\
                | \
          1  _  |  \ _ _ _ _ _ _ _
                |
                +--------------------> activeDutyCycle
                   |
            minActiveDutyCycle
    """

    mask = numpy.where(self._minActiveDutyCycles > 0)[0]
    self._boostFactors[mask] = ((1 - self.maxBoost) /
      self._minActiveDutyCycles[mask] * self._activeDutyCycles[mask]
        ).astype(realDType) + self.maxBoost

    self._boostFactors[self._activeDutyCycles >
      self._minActiveDutyCycles] = 1.0


  def _updateBookeepingVars(self, learn):
    """
    Updates counter instance variables each round.

    Parameters:
    ----------------------------
    @param learn:   a boolean value indicating whether learning should be
                    performed. Learning entails updating the  permanence
                    values of the synapses, and hence modifying the 'state'
                    of the model. setting learning to 'off' might be useful
                    for indicating separate training vs. testing sets.
    """
    self._iterationNum += 1
    if learn:
      self._iterationLearnNum += 1


  def _calculateOverlap(self, inputVector):
    """
    This function determines each column's overlap with the current input
    vector. The overlap of a column is the number of synapses for that column
    that are connected (permanence value is greater than 'synPermConnected')
    to input bits which are turned on. Overlap values that are lower than
    the 'stimulusThreshold' are ignored. The implementation takes advantage of
    the SpraseBinaryMatrix class to perform this calculation efficiently.

    Parameters:
    ----------------------------
    @param inputVector: a numpy array of 0's and 1's that comprises the input to
                    the spatial pooler.
    """
    overlaps = numpy.zeros(self._numColumns).astype(realDType)
    self._connectedSynapses.rightVecSumAtNZ_fast(inputVector, overlaps)
    overlaps[overlaps < self.stimulusThreshold] = 0
    return overlaps


  def _calculateOverlapPct(self, overlaps):
    return overlaps.astype(realDType) / self._connectedCounts


  def _inhibitColumns(self, overlaps):
    """
    Performs inhibition. This method calculates the necessary values needed to
    actually perform inhibition and then delegates the task of picking the
    active columns to helper functions.

    Parameters:
    ----------------------------
    @param overlaps: an array containing the overlap score for each  column.
                    The overlap score for a column is defined as the number
                    of synapses in a "connected state" (connected synapses)
                    that are connected to input bits which are turned on.
    """
    # determine how many columns should be selected in the inhibition phase.
    # This can be specified by either setting the 'numActiveColumnsPerInhArea'
    # parameter or the 'localAreaDensity' parameter when initializing the class
    overlaps = overlaps.copy()
    if (self.localAreaDensity > 0):
      density = self.localAreaDensity
    else:
      inhibitionArea = ((2*self._inhibitionRadius + 1)
                                    ** self.columnDimensions.size)
      inhibitionArea = min(self._numColumns, inhibitionArea)
      density = float(self.numActiveColumnsPerInhArea) / inhibitionArea
      density = min(density, 0.5)

    # Add our fixed little bit of random noise to the scores to help break ties.
    overlaps += self._tieBreaker

    if self.globalInhibition or \
      self._inhibitionRadius > max(self.columnDimensions):
      return self._inhibitColumnsGlobal(overlaps, density)
    else:
      return self._inhibitColumnsLocal(overlaps, density)


  def _inhibitColumnsGlobal(self, overlaps, density):
    """
    Perform global inhibition. Performing global inhibition entails picking the
    top 'numActive' columns with the highest overlap score in the entire
    region. At most half of the columns in a local neighborhood are allowed to
    be active.

    Parameters:
    ----------------------------
    @param overlaps: an array containing the overlap score for each  column.
                    The overlap score for a column is defined as the number
                    of synapses in a "connected state" (connected synapses)
                    that are connected to input bits which are turned on.
    @param density: The fraction of columns to survive inhibition.
    """
    #calculate num active per inhibition area

    numActive = int(density * self._numColumns)
    activeColumns = numpy.zeros(self._numColumns)
    winners = sorted(range(overlaps.size),
                     key=lambda k: overlaps[k],
                     reverse=True)[0:numActive]
    activeColumns[winners] = 1
    return numpy.where(activeColumns > 0)[0]


  def _inhibitColumnsLocal(self, overlaps, density):
    """
    Performs local inhibition. Local inhibition is performed on a column by
    column basis. Each column observes the overlaps of its neighbors and is
    selected if its overlap score is within the top 'numActive' in its local
    neighborhood. At most half of the columns in a local neighborhood are
    allowed to be active.

    Parameters:
    ----------------------------
    @param overlaps: an array containing the overlap score for each  column.
                    The overlap score for a column is defined as the number
                    of synapses in a "connected state" (connected synapses)
                    that are connected to input bits which are turned on.
    @param density: The fraction of columns to survive inhibition. This
                    value is only an intended target. Since the surviving
                    columns are picked in a local fashion, the exact fraction
                    of surviving columns is likely to vary.
    """
    activeColumns = numpy.zeros(self._numColumns)
    addToWinners = max(overlaps)/1000.0
    overlaps = numpy.array(overlaps, dtype=realDType)
    for i in xrange(self._numColumns):
      maskNeighbors = self._getNeighborsND(i, self.columnDimensions,
        self._inhibitionRadius)
      overlapSlice = overlaps[maskNeighbors]
      numActive = int(0.5 + density * (len(maskNeighbors) + 1))
      numBigger = numpy.count_nonzero(overlapSlice > overlaps[i])
      if numBigger < numActive:
        activeColumns[i] = 1
        overlaps[i] += addToWinners
    return numpy.where(activeColumns > 0)[0]


  @staticmethod
  def _getNeighbors1D(columnIndex, dimensions, radius, wrapAround=False):
    """
    Returns a list of indices corresponding to the neighbors of a given column.
    In this variation of the method, which only supports a one dimensional
    column topology, a column's neighbors are those neighbors who are 'radius'
    indices away. This information is needed to perform inhibition. This method
    is a subset of _getNeighborsND and is only included for illustration
    purposes, and potentially enhanced performance for spatial pooler
    implementations that only require a one-dimensional topology.

    Parameters:
    ----------------------------
    @param columnIndex: The index identifying a column in the permanence, potential
                    and connectivity matrices.
    @param dimensions: An array containing a dimensions for the column space. A 2x3
                    grid will be represented by [2,3].
    @param radius:  Indicates how far away from a given column are other
                    columns to be considered its neighbors. In the previous 2x3
                    example, each column with coordinates:
                    [2+/-radius, 3+/-radius] is considered a neighbor.
    @param wrapAround: A boolean value indicating whether to consider columns at
                    the border of a dimensions to be adjacent to columns at the
                    other end of the dimension. For example, if the columns are
                    laid out in one dimension, columns 1 and 10 will be
                    considered adjacent if wrapAround is set to true:
                    [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    """
    assert(dimensions.size == 1)
    ncols = dimensions[0]

    if wrapAround:
      neighbors = numpy.array(
        range(columnIndex-radius,columnIndex+radius+1)) % ncols
    else:
      neighbors = numpy.array(
        range(columnIndex-radius,columnIndex+radius+1))
      neighbors = neighbors[
        numpy.logical_and(neighbors >= 0, neighbors < ncols)]

    neighbors = list(set(neighbors) - set([columnIndex]))
    assert(neighbors)
    return neighbors


  @staticmethod
  def _getNeighbors2D(columnIndex, dimensions, radius, wrapAround=False):
    """
    Returns a list of indices corresponding to the neighbors of a given column.
    Since the permanence values are stored in such a way that information about
    topology is lost, this method allows for reconstructing the topology of the
    inputs, which are flattened to one array. Given a column's index, its
    neighbors are defined as those columns that are 'radius' indices away from
    it in each dimension. The method returns a list of the flat indices of
    these columns. This method is a subset of _getNeighborsND and is only
    included for illustration purposes, and potentially enhanced performance
    for spatial pooler implementations that only require a two-dimensional
    topology.

    Parameters:
    ----------------------------
    @param columnIndex: The index identifying a column in the permanence, potential
                    and connectivity matrices.
    @param dimensions: An array containing a dimensions for the column space. A 2x3
                    grid will be represented by [2,3].
    @param radius:  Indicates how far away from a given column are other
                    columns to be considered its neighbors. In the previous 2x3
                    example, each column with coordinates:
                    [2+/-radius, 3+/-radius] is considered a neighbor.
    @param wrapAround: A boolean value indicating whether to consider columns at
                    the border of a dimensions to be adjacent to columns at the
                    other end of the dimension. For example, if the columns are
                    laid out in one dimension, columns 1 and 10 will be
                    considered adjacent if wrapAround is set to true:
                    [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    """
    assert(dimensions.size == 2)
    nrows = dimensions[0]
    ncols = dimensions[1]

    toRow = lambda index: index / ncols
    toCol = lambda index: index % ncols
    toIndex = lambda row, col: row * ncols + col

    row = toRow(columnIndex)
    col = toCol(columnIndex)

    if wrapAround:
      colRange = numpy.array(range(col-radius, col+radius+1)) % ncols
      rowRange = numpy.array(range(row-radius, row+radius+1)) % nrows
    else:
      colRange = numpy.array(range(col-radius, col+radius+1))
      colRange = colRange[
        numpy.logical_and(colRange >= 0, colRange < ncols)]
      rowRange = numpy.array(range(row-radius, row+radius+1))
      rowRange = rowRange[
        numpy.logical_and(rowRange >= 0, rowRange < nrows)]

    neighbors = [toIndex(r, c) for (r, c) in
      itertools.product(rowRange, colRange)]
    neighbors = list(set(neighbors) - set([columnIndex]))
    assert(neighbors)
    return neighbors


  @staticmethod
  def _getNeighborsND(columnIndex, dimensions, radius, wrapAround=False):
    """
    Similar to _getNeighbors1D and _getNeighbors2D, this function Returns a
    list of indices corresponding to the neighbors of a given column. Since the
    permanence values are stored in such a way that information about topology
    is lost. This method allows for reconstructing the topology of the inputs,
    which are flattened to one array. Given a column's index, its neighbors are
    defined as those columns that are 'radius' indices away from it in each
    dimension. The method returns a list of the flat indices of these columns.
    Parameters:
    ----------------------------
    @param columnIndex: The index identifying a column in the permanence, potential
                    and connectivity matrices.
    @param dimensions: An array containing a dimensions for the column space. A 2x3
                    grid will be represented by [2,3].
    @param radius:  Indicates how far away from a given column are other
                    columns to be considered its neighbors. In the previous 2x3
                    example, each column with coordinates:
                    [2+/-radius, 3+/-radius] is considered a neighbor.
    @param wrapAround: A boolean value indicating whether to consider columns at
                    the border of a dimensions to be adjacent to columns at the
                    other end of the dimension. For example, if the columns are
                    laid out in one dimension, columns 1 and 10 will be
                    considered adjacent if wrapAround is set to true:
                    [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    """
    assert(dimensions.size > 0)

    columnCoords = numpy.unravel_index(columnIndex, dimensions)
    rangeND = []
    for i in xrange(dimensions.size):
      if wrapAround:
        curRange = numpy.array(range(columnCoords[i]-radius,
                                     columnCoords[i]+radius+1)) % dimensions[i]
      else:
        curRange = numpy.array(range(columnCoords[i]-radius,
                                     columnCoords[i]+radius+1))
        curRange = curRange[
          numpy.logical_and(curRange >= 0, curRange < dimensions[i])]

      rangeND.append(numpy.unique(curRange))

    neighbors = [numpy.ravel_multi_index(coord, dimensions) for coord in
      itertools.product(*rangeND)]
    neighbors.remove(columnIndex)
    return neighbors


  def _isUpdateRound(self):
    """
    returns true if enough rounds have passed to warrant updates of
    duty cycles
    """
    return (self._iterationNum % self._updatePeriod) == 0


  def setRandomSeed(self, seed=-1):
    """
    Initialize the random seed
    """
    if seed != -1:
      self._random = NupicRandom(seed)
    else:
      self._random = NupicRandom()


  def __setstate__(self, state):
    """
    Initialize class properties from stored values.
    """
    # original version was a float so check for anything less than 2
    if state['_version'] < 2:
      # the wrapAround property was added in version 2,
      # in version 1 the wrapAround parameter was True for SP initialization
      state['wrapAround'] = True
    # update version property to current SP version
    state['_version'] = VERSION
    self.__dict__.update(state)


  def write(self, proto):
    self._random.write(proto.random)
    proto.numInputs = self._numInputs
    proto.numColumns = self._numColumns
    cdimsProto = proto.init("columnDimensions", len(self.columnDimensions))
    for i, dim in enumerate(self.columnDimensions):
      cdimsProto[i] = int(dim)
    idimsProto = proto.init("inputDimensions", len(self.inputDimensions))
    for i, dim in enumerate(self.inputDimensions):
      idimsProto[i] = int(dim)
    proto.potentialRadius = self.potentialRadius
    proto.potentialPct = self.potentialPct
    proto.inhibitionRadius = int(self._inhibitionRadius)
    proto.globalInhibition = bool(self.globalInhibition)
    proto.numActiveColumnsPerInhArea = self.numActiveColumnsPerInhArea
    proto.localAreaDensity = self.localAreaDensity
    proto.stimulusThreshold = self.stimulusThreshold
    proto.synPermInactiveDec = self.synPermInactiveDec
    proto.synPermActiveInc = self.synPermActiveInc
    proto.synPermBelowStimulusInc = self._synPermBelowStimulusInc
    proto.synPermConnected = self.synPermConnected
    proto.minPctOverlapDutyCycles = self.minPctOverlapDutyCycles
    proto.minPctActiveDutyCycles = self.minPctActiveDutyCycles
    proto.dutyCyclePeriod = self.dutyCyclePeriod
    proto.maxBoost = self.maxBoost
    proto.wrapAround = self.wrapAround
    proto.spVerbosity = self.spVerbosity

    proto.synPermMin = self._synPermMin
    proto.synPermMax = self._synPermMax
    proto.synPermTrimThreshold = self._synPermTrimThreshold
    proto.updatePeriod = self._updatePeriod

    proto.version = self._version
    proto.iterationNum = self._iterationNum
    proto.iterationLearnNum = self._iterationLearnNum

    self._potentialPools.write(proto.potentialPools)
    self._permanences.write(proto.permanences)

    tieBreakersProto = proto.init("tieBreaker", len(self._tieBreaker))
    for i, v in enumerate(self._tieBreaker):
      tieBreakersProto[i] = float(v)

    overlapDutyCyclesProto = proto.init("overlapDutyCycles",
                                        len(self._overlapDutyCycles))
    for i, v in enumerate(self._overlapDutyCycles):
      overlapDutyCyclesProto[i] = float(v)

    activeDutyCyclesProto = proto.init("activeDutyCycles",
                                       len(self._activeDutyCycles))
    for i, v in enumerate(self._activeDutyCycles):
      activeDutyCyclesProto[i] = float(v)

    minOverlapDutyCyclesProto = proto.init("minOverlapDutyCycles",
                                           len(self._minOverlapDutyCycles))
    for i, v in enumerate(self._minOverlapDutyCycles):
      minOverlapDutyCyclesProto[i] = float(v)

    minActiveDutyCyclesProto = proto.init("minActiveDutyCycles",
                                          len(self._minActiveDutyCycles))
    for i, v in enumerate(self._minActiveDutyCycles):
      minActiveDutyCyclesProto[i] = float(v)

    boostFactorsProto = proto.init("boostFactors", len(self._boostFactors))
    for i, v in enumerate(self._boostFactors):
      boostFactorsProto[i] = float(v)


  @classmethod
  def read(cls, proto):
    sp = object.__new__(cls)
    numInputs = int(proto.numInputs)
    numColumns = int(proto.numColumns)

    sp._random = NupicRandom()
    sp._random.read(proto.random)
    sp.seed = sp._random.getSeed()
    sp._numInputs = numInputs
    sp._numColumns = numColumns
    sp.columnDimensions = numpy.array(proto.columnDimensions)
    sp.inputDimensions = numpy.array(proto.inputDimensions)
    sp.potentialRadius = proto.potentialRadius
    sp.potentialPct = proto.potentialPct
    sp._inhibitionRadius = proto.inhibitionRadius
    sp.globalInhibition = proto.globalInhibition
    sp.numActiveColumnsPerInhArea = proto.numActiveColumnsPerInhArea
    sp.localAreaDensity = proto.localAreaDensity
    sp.stimulusThreshold = proto.stimulusThreshold
    sp.synPermInactiveDec = proto.synPermInactiveDec
    sp.synPermActiveInc = proto.synPermActiveInc
    sp._synPermBelowStimulusInc = proto.synPermBelowStimulusInc
    sp.synPermConnected = proto.synPermConnected
    sp.minPctOverlapDutyCycles = proto.minPctOverlapDutyCycles
    sp.minPctActiveDutyCycles = proto.minPctActiveDutyCycles
    sp.dutyCyclePeriod = proto.dutyCyclePeriod
    sp.maxBoost = proto.maxBoost
    sp.wrapAround = proto.wrapAround
    sp.spVerbosity = proto.spVerbosity

    sp._synPermMin = proto.synPermMin
    sp._synPermMax = proto.synPermMax
    sp._synPermTrimThreshold = proto.synPermTrimThreshold
    sp._updatePeriod = proto.updatePeriod

    sp._version = VERSION
    sp._iterationNum = proto.iterationNum
    sp._iterationLearnNum = proto.iterationLearnNum

    sp._potentialPools = SparseBinaryMatrix(numInputs)
    sp._potentialPools.read(proto.potentialPools)

    sp._permanences = SparseMatrix(numColumns, numInputs)
    sp._permanences.read(proto.permanences)
    # Initialize ephemerals and make sure they get updated
    sp._connectedCounts = numpy.zeros(numColumns, dtype=realDType)
    sp._connectedSynapses = SparseBinaryMatrix(numInputs)
    sp._connectedSynapses.resize(numColumns, numInputs)
    for i in xrange(proto.numColumns):
      sp._updatePermanencesForColumn(sp._permanences.getRow(i), i, False)

    sp._tieBreaker = numpy.array(proto.tieBreaker)

    sp._overlapDutyCycles = numpy.array(proto.overlapDutyCycles,
                                          dtype=realDType)
    sp._activeDutyCycles = numpy.array(proto.activeDutyCycles,
                                         dtype=realDType)
    sp._minOverlapDutyCycles = numpy.array(proto.minOverlapDutyCycles,
                                             dtype=realDType)
    sp._minActiveDutyCycles = numpy.array(proto.minActiveDutyCycles,
                                            dtype=realDType)
    sp._boostFactors = numpy.array(proto.boostFactors, dtype=realDType)

    return sp


  def printParameters(self):
    """
    Useful for debugging.
    """
    print "------------PY  SpatialPooler Parameters ------------------"
    print "numInputs                  = ", self.getNumInputs()
    print "numColumns                 = ", self.getNumColumns()
    print "columnDimensions           = ", self.columnDimensions
    print "numActiveColumnsPerInhArea = ", self.getNumActiveColumnsPerInhArea()
    print "potentialPct               = ", self.getPotentialPct()
    print "globalInhibition           = ", self.getGlobalInhibition()
    print "localAreaDensity           = ", self.getLocalAreaDensity()
    print "stimulusThreshold          = ", self.getStimulusThreshold()
    print "synPermActiveInc           = ", self.getSynPermActiveInc()
    print "synPermInactiveDec         = ", self.getSynPermInactiveDec()
    print "synPermConnected           = ", self.getSynPermConnected()
    print "minPctOverlapDutyCycles     = ", self.getMinPctOverlapDutyCycles()
    print "minPctActiveDutyCycles      = ", self.getMinPctActiveDutyCycles()
    print "dutyCyclePeriod            = ", self.getDutyCyclePeriod()
    print "maxBoost                   = ", self.getMaxBoost()
    print "spVerbosity                = ", self.getSpVerbosity()
    print "version                    = ", self._version
