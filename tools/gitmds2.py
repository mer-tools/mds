from threading import Lock
import git
import hashlib
import os
import glob
from subprocess import Popen, PIPE

try:
    from lxml import etree
except ImportError:
    try:
        import xml.etree.cElementTree as etree
    except ImportError:
        import xml.etree.ElementTree as etree

myLock = Lock()

def synchronized(lock):
    """ Synchronization decorator. """

    def wrap(f):
        def newFunction(*args, **kw):
            lock.acquire()
            try:
                return f(*args, **kw)
            finally:
                lock.release()
        return newFunction
    return wrap

@synchronized(myLock)
def get_mappingscache():
    if not hasattr(get_mappingscache, "mcache"):
        get_mappingscache.mcache = etree.parse("packages-git/mappingscache.xml").getroot()
        get_mappingscache.mcachetime = os.stat("packages-git/mappingscache.xml").st_mtime
    stat = os.stat("packages-git/mappingscache.xml")
    if get_mappingscache.mcachetime != stat.st_mtime:
        print "mappings cache was updated, reloading.."
        get_mappingscache.mcache = etree.parse("packages-git/mappingscache.xml").getroot()
        get_mappingscache.mcachetime = os.stat("packages-git/mappingscache.xml").st_mtime
    return get_mappingscache.mcache

def git_cat(gitpath, blob):
    return Popen(["git", "--git-dir=" + gitpath, "cat-file", "blob", blob.hexsha], stdout=PIPE).communicate()[0]   

# Utilized in frontend.
#
# This basically finds the right git repository for the project
# Each project is of the form PROJECTNAME:GITREF:SUBDIR
# for example: Core:master:i586 means find metadata underneath i586/ directory in master branch in the 
# git project that project name "Core" points to in mappings.xml (MDS2 project config file)
#
# This will either return None or a dictionary with a lot of handy metadata
# such as the packages XML tree, project configuration, meta, etc.
#
def get_project(projectname):
    project = {}
    
    breakdown = projectname.split(':')
    if len(breakdown) != 3:
        return None
     
    project["obsprjname"] = projectname 
    project["prjname"] = breakdown[0]
    project["prjgitbranch"] = breakdown[1]
    project["prjsubdir"] = breakdown[2]

    doc = etree.parse("mappings.xml").getroot()
    found = False
    for x in doc.iter("mapping"):
        if x.attrib["project"] == project["prjname"]:
            found = True
            project["prjgitrepo"] = x.attrib["path"]
            break
    
    if not found:
        return None
    
    project["prjgit"] = git.Repo(project["prjgitrepo"])
    
    project["prjtree"] = project["prjgit"].tree(project["prjgitbranch"])
    
    project["packagesblob"] = project["prjtree"][project["prjsubdir"] + "/packages.xml"]

    project["prjconfblob"] = project["prjtree"][project["prjsubdir"] + "/_config"]

    project["metablob"] = project["prjtree"][project["prjsubdir"] + "/_meta"]
 
    project["packages"] = etree.fromstring(git_cat(project["prjgitrepo"], project["packagesblob"]))

    project["prjconf"] = git_cat(project["prjgitrepo"], project["prjconfblob"])

    project["meta"] = etree.fromstring(git_cat(project["prjgitrepo"], project["metablob"]))
    
    # We rename the project data inside meta to fit with the obs project name in the request
    for x in project["meta"].iter("project"):
        x.set("name", project["obsprjname"])

    return project

# Utilized in frontend
#
# Based on a project dictionary, this will return a XML document
# that corresponds to the contents of the project
#
def build_project_index(project):
    indexdoc = etree.Element('directory')
    doc = etree.ElementTree(indexdoc)

    packagesdoc = project["packages"]
    for x in packagesdoc.iter("package"):
        entryelm = etree.SubElement(indexdoc, "entry", name = x.attrib["name"])
    for x in packagesdoc.getElementsByTagName("link"):
        entryelm = etree.SubElement(indexdoc, "entry", name = x.attrib["to"])
    return etree.tostring(doc, pretty_print=True)

def get_latest_commit(project, packagename):
    packagesdoc = project["packages"]
    for x in packagesdoc.iter("package"):
        if x.attrib["name"] == packagename:
            return x.attrib["commit"]
    for x in packagesdoc.iter("link"):
        if x.attrib["to"] == packagename:
            return get_latest_commit(project, x.attrib["from"])
    return None

def get_package_commit_mtime_vrev(project, packagename):
    packagesdoc = project["packages"]
    for x in packagesdoc.iter("package"):
        if x.attrib["name"] == packagename:
            repo = git.Repo(x.attrib["git"], odbt=git.GitDB)
            return repo.commit(x.attrib["commit"]).committed_date, x.attrib["vrev"]
    for x in packagesdoc.iter("link"):
        if x.attrib["to"] == packagename:
            return get_package_commit_mtime_vrev(project, x.attrib["from"])
    return None
        

def get_entries_from_commit(project, packagename, commit):
    packagesdoc = project["packages"]
    for x in packagesdoc.iter("package"):
        if x.attrib["name"] == packagename:
            for mappingsdoc in get_mappingscache().iter("repo"):
                if mappingsdoc.attrib["path"] == x.attrib["git"]:
                    for y in mappingsdoc.iter("map"):
                        if y.attrib["commit"] == commit:
                            entries = {}
                            for z in y.iter("entry"):
                                entries[z.attrib["name"]] = z.attrib["md5"]
                            return entries
    for x in packagesdoc.iter("link"):
        if x.attrib["to"] == packagename:
            return get_entries_from_commit(project, x.attrib["from"], commit)
    return None

# Returns commit, rev, md5sum, tree
def get_package_tree_from_commit_or_rev(project, packagename, commit):
    packagesdoc = project["packages"]
    for x in packagesdoc.iter("package"):
        if x.attrib["name"] == packagename:
            followbranch = x.attrib["followbranch"]
            for mappingsdoc in get_mappingscache().iter("repo"):
                if mappingsdoc.attrib["path"] == x.attrib["git"]:
                    for y in mappingsdoc.iter("map"):
                        if y.attrib["branch"] != followbranch:
                            continue
                        if y.attrib["commit"] == commit or y.attrib["srcmd5"] == commit or y.attrib["rev"] == commit:
                            repo = git.Repo(x.attrib["git"], odbt=git.GitDB)
                            return y.attrib["commit"], y.attrib["rev"], y.attrib["srcmd5"], repo.tree(y.attrib["commit"]), x.attrib["git"]
    for x in packagesdoc.iter("link"):
        if x.attrib["to"] == packagename:
            return get_package_tree_from_commit_or_rev(project, x.attrib["from"], commit)

    return None


def get_package_index(project, packagename, getrev):
    if getrev is None:
        getrev = "latest"
    if getrev == "upload":
        getrev = "latest"
    if getrev == "build":
        getrev = "latest"
    if getrev == "latest":
        getrev = get_latest_commit(project, packagename)

    try:
        commit, rev, srcmd5, tree, git = get_package_tree_from_commit_or_rev(project, packagename, getrev)
    except TypeError:
        return None
    mtime, vrev = get_package_commit_mtime_vrev(project, packagename)
    entrymd5s = get_entries_from_commit(project, packagename, commit)

    indexdoc = etree.Element('directory', name = packagename, srcmd5 = srcmd5, rev = rev, vrev = vrev)
    doc = etree.ElementTree(indexdoc)

    # if projectpath == "obs-projects/Core-armv7l":
    #   indexdoc.childNodes[0].setAttribute("rev", str(int(rev) + 1))
    # else:
    for entry in tree:
        if entry.name == "_meta" or entry.name == "_attribute":
            continue
        etree.SubElement(indexdoc, "entry", name = entry.name,
                                            size = str(entry.size),
                                            mtime = str(mtime),
                                            md5 = entrymd5s[entry.name])
    return etree.tostring(doc, pretty_print=True)

def generate_mappings(repos):
    indexdoc = etree.Element('maps')
    doc = etree.ElementTree(indexdoc)
    
    for x in repos:
        pkgelement = etree.SubElement(indexdoc, 'repo', path = x)

        repo = git.Repo(x, odbt=git.GitDB)
        for branch in repo.heads:
            toprev = 0
            for xz in repo.iter_commits(branch):
                toprev = toprev + 1
            rev = 0

            for cm in repo.iter_commits(branch):
                entries = {}
                for entry in cm.tree:
                    if entry.name == "_meta" or entry.name == "_attribute":
                        continue
                    st = git_cat(x, entry)
                    assert len(st) == entry.size
                    m = hashlib.md5(st)
                    entries[entry.name] = m.hexdigest()
                sortedkeys = sorted(entries.keys())
                meta = ""
                for y in sortedkeys:
                    meta += entries[y]
                    meta += "  "
                    meta += y
                    meta += "\n"

                m = hashlib.md5(meta)
                mapelm = etree.SubElement( pkgelement, 'map', branch = branch.name,
                                                              commit = cm.hexsha,
                                                              srcmd5 = m.hexdigest(),
                                                              rev    = str(toprev-rev))
                for y in sortedkeys:
                    entryelm = etree.SubElement(mapelm, "entry", name = y,
                                                                 md5 = entries[y])

                rev = rev + 1
        rev = rev + 1
    return etree.tostring(doc, pretty_print=True)

def get_if_disable(project, packagename):
    packagesdoc = project["packages"]
    if packagesdoc.attrib.get("disablei586"):
        for x in packagesdoc.iter("package"):
            if x.attrib["name"] != packagename:
                continue
            if x.attrib.get("enablei586"):
                return False
        for x in packagesdoc.iter("link"):
            if x.attrib["to"] != packagename:
                continue
            if x.attrib.get("enablei586"):
                return False
        return True
    return False


def get_package_file(project, packagename, filename, getrev):
    if getrev is None:
        getrev = "latest"
    if getrev == "upload":
        getrev = "latest"
    if getrev == "build":
        getrev = "latest"
    if getrev == "latest":
        getrev = get_latest_commit(project, packagename)

    ifdisable = get_if_disable(project, packagename)
    ifdisabletxt = ""
    if ifdisable:
        ifdisabletxt = '<build><disable arch="i586" /></build>'
    # Make fake _meta file
    if filename == "_meta":
        fakemeta = """<package project="%s" name="%s">
  <title>%s</title>
  <description>Description
</description>
  <url>http://www.merproject.org</url>
  %s
</package>
""" % (project["obsprjname"], packagename, packagename, ifdisabletxt)
        #FIXME: return file_fix_meta(realproject, packagename, fakemeta, ifdisable)
        return len(fakemeta), fakemeta

    try:
        commit, rev, srcmd5, tree, git = get_package_tree_from_commit_or_rev(project, packagename, getrev)
        for entry in tree:
            if entry.name == filename:
                return entry.size, git_cat(git, entry)
    except TypeError:
        pass

    return None

def generate_mappings(cachefile):
    if os.path.exists(cachefile):
        print "cachefile exists, reusing it"
        parser = etree.XMLParser(remove_blank_text=True)
        indexdoc = etree.parse(cachefile, parser).getroot()
    else:
        print "new cachefile"
        indexdoc = etree.Element('maps')

    doc = etree.ElementTree(indexdoc)
    print "generating mappings" 

    for x in glob.iglob("packages-git/*/*"):
        if not os.path.isdir(x):
            continue

        print x
        repoelement = indexdoc.xpath("//repo[@path='%s']" % x)
        if repoelement:
            repoelement = repoelement[0]
        else:
            print "not cached"
            repoelement = etree.SubElement(indexdoc, "repo", path = x)

        repo = git.Repo(x, odbt=git.GitDB)
        for branch in repo.heads:
            print branch.name
            seenrevs = len(repoelement.xpath("./map[@branch='%s']" % branch.name))
            print "%s revs already cached" % seenrevs
            toprev = 0
            for xz in repo.iter_commits(branch):
                toprev = toprev + 1
            print "%s revs in %s" % (toprev, branch.name)
            if seenrevs == toprev:
                print "no new revs, skipping"
                continue

            rev = 0

            for cm in repo.iter_commits(branch):
                entries = {}
                for entry in cm.tree:
                    if entry.name == "_meta" or entry.name == "_attribute":
                         continue
                    st = git_cat(x, entry)
                    #assert len(st) == entry.size
                    m = hashlib.md5(st)
                    entries[entry.name] = m.hexdigest()
                sortedkeys = sorted(entries.keys())
                meta = ""
                for y in sortedkeys:
                    meta += entries[y]
                    meta += "  "
                    meta += y
                    meta += "\n"

                m = hashlib.md5(meta)
                mapelm = etree.SubElement(repoelement, "map", branch = branch.name,
                                                             commit = cm.hexsha,
                                                             srcmd5 = m.hexdigest(),
                                                             rev = str(toprev-rev))
                for y in sortedkeys:
                    entryelm = etree.SubElement(mapelm, "entry", name = y,
                                                                 md5 = entries[y])
                rev = rev + 1
                if toprev - rev == seenrevs:
                    "finished new revs"
                    break

    return doc.write(cachefile, pretty_print=True)

