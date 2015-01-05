import sys
import os
import subprocess
import shutil
import glob
import urllib2
import tarfile
import re
import numpy
import setuptools
from distutils.command.build import build
from setuptools.command.install import install

"""
This file builds and installs the NuPIC binaries.
"""

nupicCoreBucketURL = \
  "https://s3-us-west-2.amazonaws.com/artifacts.numenta.org/numenta/nupic.core"



class Setup:



  def __init__(self):
    self.repositoryDir = os.getcwd()
    self.options = self.getCommandLineOptions()
    self.platform, self.bitness = self.getPlatformInfo()



  def setup(self):
    # Build and setup NuPIC
    os.chdir(self.repositoryDir)
    nupicCoreReleaseDir = self.prepareNupicCore()
    extensions = self.getExtensionModules(nupicCoreReleaseDir)
    setuptools.setup(
      name="nupic",
      version=self.getVersion(),
      cmdclass={'build': CustomBuild, 'install': CustomInstall},
      install_requires=self.findRequirements(),
      packages=setuptools.find_packages(),
      # A lot of this stuff may not be packaged properly, most of it was added in
      # an effort to get a binary package prepared for nupic.regression testing
      # on Travis-CI, but it wasn't done the right way. I'll be refactoring a lot
      # of this for https://github.com/numenta/nupic/issues/408, so this will be
      # changing soon. -- Matt
      package_data={
        "nupic.support": ["nupic-default.xml",
                          "nupic-logging.conf"],
        "nupic": ["README.md", "LICENSE.txt"],
        "nupic.bindings": ["*.so", "*.dll", "*.i"],
        "nupic.data": ["*.json"],
        "nupic.frameworks.opf.exp_generator": ["*.json", "*.tpl"],
        "nupic.frameworks.opf.jsonschema": ["*.json"],
        "nupic.support.resources.images": [
          "*.png", "*.gif", "*.ico", "*.graffle"],
        "nupic.swarming.jsonschema": ["*.json"]
      },
      include_package_data=True,
      ext_modules=extensions,
      description="Numenta Platform for Intelligent Computing",
      author="Numenta",
      author_email="help@numenta.org",
      url="https://github.com/numenta/nupic",
      classifiers=[
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "License :: OSI Approved :: GNU General Public License (GPL)",
        "Operating System :: MacOS :: MacOS X",
        "Operating System :: POSIX :: Linux",
        # It has to be "5 - Production/Stable" or else pypi rejects it!
        "Development Status :: 5 - Production/Stable",
        "Environment :: Console",
        "Intended Audience :: Science/Research",
        "Topic :: Scientific/Engineering :: Artificial Intelligence"
      ],
      long_description = """\
Numenta Platform for Intelligent Computing: a machine intelligence platform that implements the HTM learning algorithms. HTM is a detailed computational theory of the neocortex. At the core of HTM are time-based continuous learning algorithms that store and recall spatial and temporal patterns. NuPIC is suited to a variety of problems, particularly anomaly detection and prediction of streaming data sources.

For more information, see http://numenta.org or the NuPIC wiki at https://github.com/numenta/nupic/wiki.
""")


    # Copy binaries located at nupic.core dir into source dir
    print "Copying binaries from " + nupicCoreReleaseDir + "/bin" + " to " + self.repositoryDir + "/bin..."
    if not os.path.exists(self.repositoryDir + "/bin"):
      os.makedirs(self.repositoryDir + "/bin")
    shutil.copy(nupicCoreReleaseDir + "/bin/py_region_test", self.repositoryDir + "/bin")

    # Copy bindings located at build dir into source dir
    buildDir = glob.glob(self.repositoryDir + "/build/lib.*/")[0]
    bindingsBuildDir = buildDir + "/nupic/bindings"
    bindingsSourceDir = self.repositoryDir + "/nupic/bindings"
    bindingLibraries = ["engine_internal", "algorithms", "math"]
    print "Copying libraries from " + buildDir + " to " + self.repositoryDir + "..."
    for library in bindingLibraries:
      shutil.copy(bindingsBuildDir + "/" + library + ".py", bindingsSourceDir)
      shutil.copy(bindingsBuildDir + "/" + "_" + library + self.getSharedLibExtension(),
                  bindingsSourceDir)

    # Copy proto files located at build dir into source dir
    protoBuildDir = nupicCoreReleaseDir + "/include/nupic/proto"
    protoSourceDir = bindingsBuildDir + "/proto"
    if not os.path.exists(protoSourceDir):
      os.makedirs(protoSourceDir)
    for file in glob.glob(protoBuildDir + "/*.capnp"):
      shutil.copy(file, protoSourceDir)

    # Copy cpp_region located at build dir into source dir
    shutil.copy(buildDir + "/nupic/" + self.getLibPrefix() + "cpp_region" +
                self.getSharedLibExtension(), self.repositoryDir + "/nupic")



  def getCommandLineOptions(self):

    # optionDesc = [name, value, description]
    optionsDesc = []
    optionsDesc.append(
      ["nupic-core-dir",
       "dir",
       "(optional) Absolute path to nupic.core binary release directory"]
    )
    optionsDesc.append(
      ["skip-compare-versions",
       "",
       "(optional) Skip nupic.core version comparison"]
    )
    optionsDesc.append(
      ["user-make-command",
       "file",
       "(optional) Default `make` command used to build nupic.core"]
    )

    # Read command line options looking for extra options
    # For example, an user could type:
    #   python setup.py install --user-make-command="usr/bin/make"
    # which will set the Make executable
    optionsValues = dict()
    for arg in sys.argv[:]:
      optionFound = False
      for option in optionsDesc:
        name = option[0]
        if "--" + name in arg:
          value = None
          hasValue = (option[1] != "")
          if hasValue:
            (_, _, value) = arg.partition("=")

          optionsValues[name] = value
          sys.argv.remove(arg)
          optionFound = True
          break
      if not optionFound:
        if ("--help-nupic" in arg):
          self.printOptions(optionsDesc)
          sys.exit()

    # Check if no option was passed, i.e. if "setup.py" is the only option
    # If True, "develop" is passed by default. This is useful when a developer
    # wishes to build the project directly from an IDE.
    if len(sys.argv) == 1:
      print "No command passed. Using 'develop' as default command. Use " \
            "'python setup.py --help' for more information."
      sys.argv.append("develop")

    return optionsValues



  def getCommandLineOption(self, name):
    if name in self.options:
      return self.options[name]



  def printOptions(self, optionsDesc):
    """
    Print command line options.
    """

    print "Options:\n"
    for option in optionsDesc:
      optionUsage = "--" + option[0]
      if option[1] != "":
        optionUsage += "=[" + option[1] + "]"
      optionDesc = option[2]
      print "    " + optionUsage.ljust(30) + " = " + optionDesc



  def getPlatformInfo(self):
    """
    Identify platform
    """

    if "linux" in sys.platform:
      platform = "linux"
    elif "darwin" in sys.platform:
      platform = "darwin"
    elif "win" in sys.platform:
      platform = "windows"
    else:
      raise Exception("Platform '%s' is unsupported!" % sys.platform)

    if sys.maxsize > 2**32:
      bitness = "64"
    else:
      bitness = "32"

    return platform, bitness



  def getVersion(self):
    """
    Get version from local file.
    """
    with open("VERSION", "r") as versionFile:
      return versionFile.read().strip()



  def findRequirements(self):
    """
    Read the requirements.txt file and parse into requirements for setup's
    install_requirements option.
    """
    requirementsPath = os.path.join(self.repositoryDir, "external/common/requirements.txt")
    return [
      line.strip()
      for line in open(requirementsPath).readlines()
      if not line.startswith("#")
    ]



  def getExtensionModules(self, nupicCoreReleaseDir):

    #
    # Gives the version of Python necessary to get installation directories
    # for use with pythonVersion, etc.
    #
    if sys.version_info < (2, 7):
      raise Exception("Fatal Error: Python 2.7 or later is required.")

    pythonVersion = str(sys.version_info[0]) + '.' + str(sys.version_info[1])

    #
    # Find out where system installation of python is.
    #
    pythonPrefix = sys.prefix
    pythonPrefix = pythonPrefix.replace("\\", "/")
    pythonIncludeDir = pythonPrefix + "/include/python" + pythonVersion

    #
    # Finds out version of Numpy and headers' path.
    #
    numpyIncludeDir = numpy.get_include()
    numpyIncludeDir = numpyIncludeDir.replace("\\", "/")

    commonDefines = [
      ("NUPIC2", None),
      ("NTA_PLATFORM_" + self.platform + self.bitness, None),
      ("NTA_PYTHON_SUPPORT", pythonVersion),
      ("NTA_INTERNAL", None),
      ("NTA_ASSERTIONS_ON", None),
      ("NTA_ASM", None),
      ("HAVE_CONFIG_H", None),
      ("BOOST_NO_WREGEX", None)]

    commonIncludeDirs = [
      self.repositoryDir + "/external/" +
        self.platform + self.bitness + "/include",
      self.repositoryDir + "/external/common/include",
      self.repositoryDir + "/extensions",
      self.repositoryDir,
      nupicCoreReleaseDir + "/include",
      pythonIncludeDir,
      numpyIncludeDir]

    commonCompileFlags = [
      # Adhere to c++11 spec
      "-std=c++11",
      # Generate 32 or 64 bit code
      "-m" + self.bitness,
      # `position independent code`, required for shared libraries
      "-fPIC",
      "-fvisibility=hidden",
      "-Wall",
      "-Wreturn-type",
      "-Wunused",
      "-Wno-unused-parameter"]
    if self.platform == "darwin":
      commonCompileFlags.append("-stdlib=libc++")

    commonLinkFlags = [
      "-m" + self.bitness,
      "-fPIC",
      "-L" + pythonPrefix + "/lib"]

    commonLibraries = []
    if self.platform == "linux":
      commonLibraries.extend(["pthread", "dl"])

    commonObjects = [
      nupicCoreReleaseDir + "/lib/" +
        self.getLibPrefix() + "nupic_core" + self.getStaticLibExtension()]

    pythonSupportSources = [
      "extensions/py_support/NumpyVector.cpp",
      "extensions/py_support/PyArray.cpp",
      "extensions/py_support/PyHelpers.cpp",
      "extensions/py_support/PythonStream.cpp"]

    extensions = []

    libDynamicCppRegion = setuptools.Extension(
      "nupic." + self.getLibPrefix() + "cpp_region",
      extra_compile_args=commonCompileFlags,
      define_macros=commonDefines,
      extra_link_args=commonLinkFlags,
      include_dirs=commonIncludeDirs,
      libraries =
        commonLibraries +
        ["dl",
        "python" + pythonVersion],
      sources=pythonSupportSources +
        ["extensions/cpp_region/PyRegion.cpp",
        "extensions/cpp_region/unittests/PyHelpersTest.cpp"],
      extra_objects=commonObjects)
    extensions.append(libDynamicCppRegion)

    # TODO: Find way to include HtmTest executable as extension. Not sure if this is possible -- David

    #
    # SWIG
    #
    swigDir = self.repositoryDir + "/external/common/share/swig/3.0.2"
    swigExecutable = self.repositoryDir + "/external/" + self.platform \
                     + self.bitness + "/bin/swig"
    buildCommands = ["build", "build_ext", "install", "install_lib", "develop"]
    for arg in sys.argv:
      if arg in buildCommands:
        sys.argv.extend(["build_ext", "--swig", swigExecutable])
        break

    swigFlags = [
      "-c++",
      "-features",
      "autodoc=0,directors=0",
      "-noproxyimport",
      "-keyword",
      "-modern",
      "-modernargs",
      "-noproxydel",
      "-fvirtual",
      "-fastunpack",
      "-nofastproxy",
      "-fastquery",
      "-outputtuple",
      "-castmode",
      "-w402",
      "-w503",
      "-w511",
      "-w302",
      "-w362",
      "-w312",
      "-w389",
      "-DSWIG_PYTHON_LEGACY_BOOL",
      "-DNTA_PLATFORM_" + self.platform + self.bitness,
      "-I" + self.repositoryDir + "/extensions",
      "-I" + nupicCoreReleaseDir + "/include",
      "-I" + swigDir + "/python",
      "-I" + swigDir]

    swigLibraries = [
      "dl",
      "python" + pythonVersion]

    libModuleAlgorithms = setuptools.Extension(
      "nupic.bindings._algorithms",
      swig_opts=swigFlags,
      extra_compile_args=commonCompileFlags,
      define_macros=commonDefines,
      extra_link_args=commonLinkFlags,
      include_dirs=commonIncludeDirs,
      libraries=swigLibraries,
      sources=pythonSupportSources +
        ["nupic/bindings/algorithms.i"],
      extra_objects=commonObjects)
    extensions.append(libModuleAlgorithms)

    libModuleEngineInternal = setuptools.Extension(
      "nupic.bindings._engine_internal",
      swig_opts=swigFlags,
      extra_compile_args=commonCompileFlags,
      define_macros=commonDefines,
      extra_link_args=commonLinkFlags,
      include_dirs=commonIncludeDirs,
      libraries=swigLibraries,
      sources=pythonSupportSources +
        ["nupic/bindings/engine_internal.i"],
      extra_objects=commonObjects)
    extensions.append(libModuleEngineInternal)

    libModuleMath = setuptools.Extension(
      "nupic.bindings._math",
      swig_opts=swigFlags,
      extra_compile_args=commonCompileFlags,
      define_macros=commonDefines,
      extra_link_args=commonLinkFlags,
      include_dirs=commonIncludeDirs,
      libraries=swigLibraries,
      sources=pythonSupportSources +
        ["nupic/bindings/math.i",
        "nupic/bindings/PySparseTensor.cpp"],
      extra_objects=commonObjects)
    extensions.append(libModuleMath)

    return extensions



  def getLibPrefix(self):
    """
    Returns the default system prefix of a compiled library.
    """
    if self.platform == "linux" or self.platform == "darwin":
      return "lib"
    elif self.platform == "windows":
      return ""



  def getStaticLibExtension(self):
    """
    Returns the default system extension of a compiled static library.
    """
    if self.platform == "linux" or self.platform == "darwin":
      return ".a"
    elif self.platform == "windows":
      return ".lib"



  def getSharedLibExtension(self):
    """
    Returns the default system extension of a compiled shared library.
    """
    if self.platform == "linux" or self.platform == "darwin":
      return ".so"
    elif self.platform == "windows":
      return ".dll"



  def extractNupicCoreTarget(self):
    # First, get the nupic.core SHA and remote location from local config.
    nupicConfig = {}
    if os.path.exists(self.repositoryDir + "/.nupic_config"):
      execfile(
        os.path.join(self.repositoryDir, ".nupic_config"), {}, nupicConfig
      )
    elif os.path.exists(os.environ["HOME"] + "/.nupic_config"):
      execfile(
        os.path.join(os.environ["HOME"], ".nupic_config"), {}, nupicConfig
      )
    else:
      execfile(
        os.path.join(self.repositoryDir, ".nupic_modules"), {}, nupicConfig
      )
    return nupicConfig["NUPIC_CORE_REMOTE"], nupicConfig["NUPIC_CORE_COMMITISH"]



  # Returns nupic.core release directory and source directory in tuple.
  def getDefaultNupicCoreDirectories(self):
    # Default nupic.core location is relative to the NuPIC checkout.
    return self.repositoryDir + "/extensions/core/build/release", \
           self.repositoryDir + "/extensions/core"



  def buildNupicCoreFromGitClone(self, nupicCoreCommitish,
                                 nupicCoreLocalPackage, nupicCoreReleaseDir,
                                 nupicCoreRemote, nupicCoreSourceDir):
    print ("Building nupic.core from local checkout "
           + nupicCoreSourceDir + "...")
    # Remove the local package file, which didn't get populated due to the
    # download failure.
    if os.path.exists(nupicCoreLocalPackage):
      os.remove(nupicCoreLocalPackage)

    # Get nupic.core dependency through git.
    if not os.path.exists(nupicCoreSourceDir + "/.git"):
      # There's not a git repo in nupicCoreSourceDir, so we can blow the
      # whole directory away and clone nupic.core there.
      shutil.rmtree(nupicCoreSourceDir, True)
      os.makedirs(nupicCoreSourceDir)
      cloneCommand = ["git", "clone", nupicCoreRemote, nupicCoreSourceDir, "--depth=50"]
      print " ".join(cloneCommand)
      process = subprocess.Popen(cloneCommand,
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE)
      process.communicate()
      if process.returncode != 0:
        raise Exception("Fatal Error: Unable to clone %s into %s"
                        % (nupicCoreRemote, nupicCoreSourceDir))
    else:
      # Fetch if already cloned.
      gitFetchCmd = ["git", "fetch", nupicCoreRemote]
      print " ".join(gitFetchCmd)
      process = subprocess.Popen(gitFetchCmd,
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE,
                                 cwd=nupicCoreSourceDir)
      process.communicate()
      if process.returncode != 0:
        raise Exception("Fatal Error: Unable to fetch %s"
                        % nupicCoreRemote)

    # Get the exact SHA we need for nupic.core.
    gitResetCmd = ["git", "reset", "--hard", nupicCoreCommitish]
    print " ".join(gitResetCmd)
    process = subprocess.Popen(gitResetCmd,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE,
                               cwd=nupicCoreSourceDir)
    process.communicate()
    if process.returncode != 0:
      raise Exception("Fatal Error: Unable to checkout %s in %s"
                      % (nupicCoreCommitish, nupicCoreSourceDir))

    # Execute the Make scripts
    process = subprocess.Popen(["git", "clean", "-fdx"],
                               stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE,
                               cwd=nupicCoreSourceDir)
    process.communicate()
    if process.returncode != 0:
      raise Exception(
        "Fatal Error: Compiling 'nupic.core' library within %s failed."
        % self.repositoryDir
      )

    # Build and set external libraries
    print "Building 'nupic.core' library..."
    makeWorkingDir = "%s/build/scripts" % nupicCoreSourceDir
    # Clean 'build/scripts' subfolder at submodule folder
    shutil.rmtree(nupicCoreSourceDir + "/build/scripts", True)
    os.makedirs(nupicCoreSourceDir + "/build/scripts")
    shutil.rmtree(nupicCoreReleaseDir, True)
    # Generate the Make scripts
    cmakeCmd = ["cmake",
                "%s/src" % nupicCoreSourceDir,
                "-DCMAKE_INSTALL_PREFIX=%s" % nupicCoreReleaseDir]
    print " ".join(cmakeCmd)
    process = subprocess.Popen(cmakeCmd,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE,
                               cwd=makeWorkingDir)
    cmakeStdOut, cmakeStdErr = process.communicate()
    if process.returncode != 0:
      print cmakeStdErr
      raise Exception(
        "Fatal Error: cmake command failed in %s!"
        % makeWorkingDir
      )
    else:
      print cmakeStdOut
      print "CMake complete."

    # Execute the Make scripts
    if "user-make-command" in self.options:
      userMakeCommand = [self.options["user-make-command"]]
    else:
      userMakeCommand = ["make"]
    userMakeCommand = userMakeCommand + ["install", "-j4"]
    print " ".join(userMakeCommand)
    process = subprocess.Popen(userMakeCommand,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE,
                               cwd=makeWorkingDir)
    makeStdOut, makeStdErr = process.communicate()
    if process.returncode != 0:
      print makeStdErr
      raise Exception(
        "Fatal Error: make command failed in %s!"
        % makeWorkingDir
      )
    else:
      print makeStdOut
      print "Make complete."
    print "Done building nupic.core."



  def prepareNupicCore(self):

    nupicCoreReleaseDir = self.getCommandLineOption("nupic-core-dir")
    nupicCoreSourceDir = None
    fetchNupicCore = True

    if nupicCoreReleaseDir:
      # User specified that they have their own nupic.core
      fetchNupicCore = False
    else:
      nupicCoreReleaseDir, nupicCoreSourceDir = \
        self.getDefaultNupicCoreDirectories()

    nupicCoreRemote, nupicCoreCommitish = self.extractNupicCoreTarget()

    if fetchNupicCore:
      # User has not specified 'nupic.core' location, so we'll download the
      # binaries.

      nupicCoreRemoteUrl = (nupicCoreBucketURL + "/nupic_core-"
                            + nupicCoreCommitish + "-" + self.platform
                            + self.bitness + ".tar.gz")
      nupicCoreLocalPackage = (nupicCoreSourceDir + "/nupic_core-"
                               + nupicCoreCommitish + "-" + self.platform
                               + self.bitness + ".tar.gz")
      nupicCoreLocalDirToUnpack = ("nupic_core-"
                                   + nupicCoreCommitish + "-" + self.platform
                                   + self.bitness)

      if os.path.exists(nupicCoreLocalPackage):
        print ("Target nupic.core package already exists at "
               + nupicCoreLocalPackage + ".")
        self.unpackFile(
          nupicCoreLocalPackage, nupicCoreLocalDirToUnpack, nupicCoreReleaseDir
        )
      else:
        print "Attempting to fetch nupic.core binaries..."
        downloadSuccess = self.downloadFile(
          nupicCoreRemoteUrl, nupicCoreLocalPackage
        )

        # TODO: Give user a way to clean up all the downloaded binaries. It can
        # be manually done with `rm -rf $NUPIC_CORE/extensions/core` but would
        # be cleaner with something like `python setup.py clean`.

        if not downloadSuccess:
          print ("WARNING:\n\tRemote nupic.core download of %s failed!"
                 "\n\tBuilding nupic.core locally from SHA: %s.\n"
                 % (nupicCoreRemoteUrl, nupicCoreCommitish))
          self.buildNupicCoreFromGitClone(nupicCoreCommitish,
                                          nupicCoreLocalPackage,
                                          nupicCoreReleaseDir, nupicCoreRemote,
                                          nupicCoreSourceDir)

        else:
          print "Download successful."
          self.unpackFile(nupicCoreLocalPackage,
                          nupicCoreLocalDirToUnpack,
                          nupicCoreReleaseDir)

    else:
      print "Using nupic.core binaries at " + nupicCoreReleaseDir

    if "skip-compare-versions" in self.options:
      skipCompareVersions = True
    else:
      skipCompareVersions = not fetchNupicCore

    if not skipCompareVersions:
      # Compare expected version of nupic.core against installed version
      file = open(nupicCoreReleaseDir + "/include/nupic/Version.hpp", "r")
      content = file.read()
      file.close()
      nupicCoreVersionFound = re.search(
        "#define NUPIC_CORE_VERSION \"([a-z0-9]+)\"", content
      ).group(1)

      if nupicCoreCommitish != nupicCoreVersionFound:
        raise Exception(
          "Fatal Error: Unexpected version of nupic.core! "
          "Expected %s, but detected %s."
          % (nupicCoreCommitish, nupicCoreVersionFound)
        )

    return nupicCoreReleaseDir



  def downloadFile(self, url, destFile, silent=False):
    """
    Download a file to the specified location
    """

    if not silent:
      print "Downloading from\n\t%s\nto\t%s.\n" % (url, destFile);

    destDir = os.path.dirname(destFile)
    if not os.path.exists(destDir):
      os.makedirs(destDir)

    try:
      response = urllib2.urlopen(url)
    except urllib2.URLError:
      return False

    file = open(destFile, "wb")

    totalSize = response.info().getheader('Content-Length').strip()
    totalSize = int(totalSize)
    bytesSoFar = 0

    # Download chunks writing them to target file
    chunkSize = 8192
    oldPercent = 0
    while True:
      chunk = response.read(chunkSize)
      bytesSoFar += len(chunk)

      if not chunk:
        break

      file.write(chunk)

      # Show progress
      if not silent:
        percent = (float(bytesSoFar) / totalSize) * 100
        percent = int(percent)
        if percent != oldPercent and percent % 5 == 0:
          print ("Downloaded %i of %i bytes (%i%%)."
                 % (bytesSoFar, totalSize, int(percent)))
          oldPercent = percent

    file.close()

    return True



  def unpackFile(self, package, dirToUnpack, destDir, silent=False):
    """
    Unpack package file to the specified directory
    """

    if not silent:
      print "Unpacking %s into %s..." % (package, destDir)

    file = tarfile.open(package, 'r:gz')
    file.extractall(destDir)
    file.close()

    # Copy subdirectories to a level up
    subDirs = os.listdir(destDir + "/" + dirToUnpack)
    for dir in subDirs:
      shutil.rmtree(destDir + "/" + dir, True)
      shutil.move(destDir + "/" + dirToUnpack + "/" + dir, destDir + "/" + dir)
    shutil.rmtree(destDir + "/" + dirToUnpack, True)



class CustomBuild(build):
  def run(self):
    # Compile extensions before python modules to avoid that SWIG generated
    # modules get out of the dist
    self.run_command('build_ext')
    build.run(self)



class CustomInstall(install):
  def run(self):
    # Compile extensions before python modules to avoid that SWIG generated
    # modules get out of the dist
    self.run_command('build_ext')
    self.do_egg_install()



if __name__ == '__main__':
  setup = Setup()
  setup.setup()
