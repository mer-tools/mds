from threading import Lock
import git
import hashlib
import os
import glob
import csv
from subprocess import Popen, PIPE
import logging

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
log = logging.getLogger("mds2.git")

try:
    from lxml import etree
except ImportError:
    try:
        import xml.etree.cElementTree as etree
    except ImportError:
        import xml.etree.ElementTree as etree

mappingsCacheLock = Lock()

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

@synchronized(mappingsCacheLock)
def get_mappingscache():
    if not hasattr(get_mappingscache, "mcache"):
        get_mappingscache.mcache = etree.parse("mappingscache.xml").getroot()
        get_mappingscache.mcachetime = os.stat("mappingscache.xml").st_mtime
    stat = os.stat("mappingscache.xml")
    if get_mappingscache.mcachetime != stat.st_mtime:
        log.info("mappings cache was updated, reloading..")
        get_mappingscache.mcache = etree.parse("mappingscache.xml").getroot()
        get_mappingscache.mcachetime = os.stat("mappingscache.xml").st_mtime
    return get_mappingscache.mcache

mappingsLock = Lock()

@synchronized(mappingsLock)
def get_mappings():
    if not hasattr(get_mappings, "mcache"):
        get_mappings.mcache = etree.parse("mappings.xml").getroot()
        get_mappings.mcachetime = os.stat("mappings.xml").st_mtime
    stat = os.stat("mappings.xml")
    if get_mappings.mcachetime != stat.st_mtime:
        log.info("mappings.xml was updated, reloading..")
        get_mappings.mcache = etree.parse("mappings.xml").getroot()
        get_mappings.mcachetime = os.stat("mappings.xml").st_mtime
    return get_mappings.mcache

lasteventsLock = Lock()

@synchronized(lasteventsLock)
def get_lastevents():
    if not hasattr(get_lastevents, "mcache"):
        get_lastevents.mcache = etree.parse("lastevents.xml").getroot()
        get_lastevents.mcachetime = os.stat("lastevents.xml").st_mtime
        get_lastevents.ecount = len(get_lastevents.mcache.xpath("//event"))
    stat = os.stat("lastevents.xml")
    if get_lastevents.mcachetime != stat.st_mtime:
        log.info("lastevents.xml was updated, reloading..")
        get_lastevents.mcache = etree.parse("lastevents.xml").getroot()
        get_lastevents.mcachetime = os.stat("lastevents.xml").st_mtime
        get_lastevents.ecount = len(get_lastevents.mcache.xpath("//event"))
    return get_lastevents.mcache

def lookup_binariespath(projectname):
    binaries_path = None
    for x in get_mappings().iter("mapping"):
        #if x.attrib["project"] == projectname:
        binaries_path = os.path.join(x.attrib["binaries"], projectname)
    if os.path.exists(binaries_path):
        return binaries_path
    else:
        return None

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

    found = False
    for x in get_mappings().iter("mapping"):
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
    for x in packagesdoc.iter("link"):
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
        #commit, rev, srcmd5, tree, git = get_package_tree_from_commit_or_rev(project, packagename, getrev)
        commit, rev, srcmd5, tree, _ = get_package_tree_from_commit_or_rev(project, packagename, getrev)
    except TypeError:
        return ""
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

    return ""

def get_next_event():
        return get_lastevents.ecount + 1

def get_events_filtered(start, filters):
    indexdoc = etree.Element('events', next = str(get_next_event()))
    impl = etree.ElementTree(indexdoc)
    #filters to xpath
    xpaths = []
    for filtr in filters:
        extra = ""
        if filtr[2]:
            extra = " and %s='%s'" % (filtr[0], filtr[2])
        xpaths.append("./following-sibling::*[@type='%s' and project='%s'%s]" % (filtr[0], filtr[1], extra))
    if xpaths:
        # union of all filters
        xpaths = " | ".join(xpaths)
    else:
        xpaths = "./following-sibling::*"

    # get starting event
    start_event = get_lastevents().xpath("//event[position()=%s]" % start)[0]
    filtered = start_event.xpath(xpaths)
    indexdoc.extend(filtered)

    return etree.tostring(indexdoc, pretty_print=True)

def update_lastevents(project, branch, cm, entry, events):

    blobs = [entry]
    subprj = ":".join([project, branch.name])
    # if this is a tree (subdir) get blobs one level in
    if entry.type == "tree":
        blobs = entry.blobs
        subprj = ":".join([subprj, entry.path])
    
    for blob in blobs:
        # only want blobs that are in the list, no deeper subdirs
        if blob.type != "blob":
            continue
        # _config and _meta changes trigger project events
        if blob.name == "_config" or blob.name == "_meta":
            log.debug("project %s event" % subprj)
            eventelm = etree.SubElement(events, "event", type = "project")
            prjelm = etree.SubElement(eventelm, "project")
            prjelm.text = subprj
        # packages.xml changes trigger package events
        elif blob.name == "packages.xml":
            difflist = []
            try:
                # diff to previous commit, with least possible cruft, discarding header
                difflist = cm.diff(other=cm.hexsha+"~1", paths=[blob.path], create_patch=True, U=0)
            except git.exc.GitCommandError:
                # this exception will be raised in some cases such as the initial commit where there is no previous commit
                pass
            if difflist:
                pdiff = difflist[0].diff.splitlines()[3:]
                for line in pdiff:
                    # lines starting with - mean either package was updated or removed
                    if line.startswith("-") and line[1:].strip():
                        # extract the package name, or link target from the xml element
                        pkg = None
                        log.debug(line)
                        try:
                            pelem = etree.fromstring(line[1:].strip())
                            log.debug(etree.tostring(pelem))
                            if pelem.tag == "package":
                                pkg = pelem.attrib['name']
                            elif pelem.tag == "link":
                                pkg = pelem.attrib['to']
                        except etree.XMLSyntaxError:
                            pass
    
                        if pkg:
                            log.debug("project %s package %s event" % (subprj, pkg))
                            eventelm = etree.SubElement(events, "event", type = "package")
                            prjelm = etree.SubElement(eventelm, "project")
                            prjelm.text = subprj
                            pkgelm = etree.SubElement(eventelm, "package")
                            pkgelm.text = pkg
        #FIXME: repo events ?

def initial_lastevents(project, branch, events):

    subprj = ":".join([project, branch.name])
    # Get root tree of branch
    root = branch.repo.tree()
    # collect blobs from root and subdirs
    blobs = []
    for tree in root.trees:
        if tree.type == "blob":
            blobs.append(tree)
        elif tree.type == "tree":
            blobs.extend(tree.blobs)
    # generate initial events for blobs we care about
    for blob in blobs:
        if os.path.dirname(blob.path):
            subprj = ":".join([project, branch.name, os.path.dirname(blob.path)])
        if blob.name == "_config" or blob.name == "_meta":
            log.debug("project %s event" % subprj)
            eventelm = etree.SubElement(events, "event", type = "project")
            prjelm = etree.SubElement(eventelm, "project")
            prjelm.text = subprj
        # packages.xml changes trigger package events
        elif blob.name == "packages.xml":
            pxml = etree.fromstring(git_cat(branch.repo.working_dir, blob))
            for pkg in pxml.iter("package"):
                pkg_name = pkg.attrib['name']
                log.debug("project %s package %s event" % (subprj, pkg_name))
                eventelm = etree.SubElement(events, "event", type = "package")
                prjelm = etree.SubElement(eventelm, "project")
                prjelm.text = subprj
                pkgelm = etree.SubElement(eventelm, "package")
                pkgelm.text = pkg_name
            for pkg in pxml.iter("link"):
                pkg_name = pkg.attrib['to']
                log.debug("project %s package %s event" % (subprj, pkg_name))
                eventelm = etree.SubElement(events, "event", type = "package")
                prjelm = etree.SubElement(eventelm, "project")
                prjelm.text = subprj
                pkgelm = etree.SubElement(eventelm, "package")
                pkgelm.text = pkg_name
        #FIXME: repo events ?

def generate_mappings(cachefile, eventsfile):

    if os.path.exists(cachefile) and os.path.exists(eventsfile):
        log.info("%s exists, reusing it" % cachefile)
        parser = etree.XMLParser(remove_blank_text=True)
        maps = etree.parse(cachefile, parser).getroot()
        events = etree.parse(eventsfile, parser).getroot()
        initial = False
    else:
        log.info("creating new %s" % cachefile)
        maps = etree.Element('maps')
        events = etree.Element('events')
        initial = True

    mapdoc = etree.ElementTree(maps)
    eventdoc = etree.ElementTree(events)

    for x in glob.iglob("*-git/*/*"):
        # filter out non directories
        if not os.path.isdir(x):
            continue

        # if this git repo is a "projects repo" turn on generating events
        project = None
        prjmap = get_mappings().xpath("//mapping[@path='%s']" % x)
        if prjmap:
            project = prjmap[0].attrib.get("project", None)
            binaries = prjmap[0].attrib.get("binaries", None)

        log.debug(x)
        repoelement = maps.xpath("//repo[@path='%s']" % x)
        if repoelement:
            repoelement = repoelement[0]
        else:
            log.debug("not cached")
            repoelement = etree.SubElement(maps, "repo", path = x)

        repo = git.Repo(x, odbt=git.GitDB)
        for branch in repo.heads:
            log.debug(branch.name)
            seenrevs = len(repoelement.xpath("./map[@branch='%s']" % branch.name))
            log.debug("%s revs already cached" % seenrevs)
            toprev = 0
            for xz in repo.iter_commits(branch):
                toprev = toprev + 1
            log.debug("%s revs in %s" % (toprev, branch.name))
            if seenrevs == toprev:
                log.debug("no new revs, skipping")
                continue

            rev = 0

            # if generating events from scratch for projects
            if project and initial:
                initial_lastevents(project, branch, events)

            for cm in repo.iter_commits(branch):
                entries = {}
                for entry in cm.tree:

                    blobs = [entry]
                    # if project is set, generate appropriate events for this commit

                    if project and not initial:
                        update_lastevents(project, branch, cm, entry, events)

                    for blob in blobs:
                        # only want blobs that are in the list, no deeper subdirs
                        if blob.type != "blob":
                            continue

                        # _meta and _attribute files are not included in mappings cache
                        if blob.name == "_meta" or blob.name == "_attribute":
                            continue

                        #FIXME: is there a smarter way to md5sum hash an object without loading the whole thing in memory ?
                        st = git_cat(x, blob)
                        #assert len(st) == entry.size
                        m = hashlib.md5(st)
                        entries[blob.path] = m.hexdigest()

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
                    log.debug("finished new revs")
                    break

    mapdoc.write(cachefile, pretty_print=True)
    eventdoc.write(eventsfile, pretty_print=True)
    log.info("%s now up to date" % cachefile)

