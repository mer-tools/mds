#!/usr/bin/python

__version__ = "2.0"

import os, sys
import SocketServer
import BaseHTTPServer
import time
import shutil
import urlparse
import urllib
import gitmds2
import traceback
import subprocess
import threading
import signal
import logging
import argparse

try:
    from lxml import etree
except ImportError:
    try:
        import xml.etree.cElementTree as etree
    except ImportError:
        import xml.etree.ElementTree as etree

try:
    from cStringIO import StringIO
except ImportError:
    from StringIO import StringIO

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
log = logging.getLogger("mds2.api")

# Handy helpers:
#  This converts a string into a stream and returns the size and the content stream
def string2stream(thestr):
    content = StringIO()
    content.write(thestr)
    content.seek(0, os.SEEK_END)
    contentsize = content.tell()
    content.seek(0, os.SEEK_SET)
    return contentsize, content

def file2stream(path):
    f = open(path, 'rb')
    fs = os.fstat(f.fileno())
    return fs[6], fs.st_mtime, f

def copyfile(source, outputfile):
    """Copy all data between two file objects.

    The SOURCE argument is a file object open for reading
    (or anything with a read() method) and the DESTINATION
    argument is a file object open for writing (or
    anything with a write() method).

    The only reason for overriding this would be to change
    the block size or perhaps to replace newlines by CRLF
    -- note however that this the default server uses this
    to copy binary data as well.

    """
    shutil.copyfileobj(source, outputfile)

def chunk_generator(source, chunksize):
    while True:
        data = source.read(chunksize)
        if not data:
            break
        yield data

def chunkfile(source, outputfile, chunksize=8192):
    for chunk in chunk_generator(source, chunksize):
        outputfile.write('%X\r\n' % len(chunk))
        outputfile.write(chunk)
        outputfile.write('\r\n')
    outputfile.write('0\r\n\r\n')

# MDSHTTPRequestHandler handles the incoming HTTP requests
# The basic flow is that GET/POST/HEAD attempts to 
# run the send_head command, and if that throws an exception,
# a 500 will be sent
# 
# send_head returns a content stream, or None if things fail

class MDSHTTPRequestHandler(BaseHTTPServer.BaseHTTPRequestHandler):
    server_version = "mds/" + __version__
    protocol_version = 'HTTP/1.1'

    def do_GET(self):
        """Serve a GET request."""
        f = None
        try:
            func, f = self.send_head()
        except: 
            self.send_response(500)
            log.warn("500: " + self.path)
            traceback.print_exc(file=sys.stdout)
            self.end_headers()
        if f:
            func(f, self.wfile)
            if hasattr(f, "close"):
                f.close()

    def do_HEAD(self):
        """Serve a HEAD request."""
        f = None
        try:
            func, f = self.send_head()
        except:
            if f:
                if hasattr(f, "close"):
                    f.close()

    def do_POST(self):
        f = None
        try:
            func, f = self.send_head()
        except:
            self.send_response(500)
            log.info("500: " + self.path)
            traceback.print_exc(file=sys.stdout)
            self.end_headers()
        if f:
            func(f, self.wfile)
            if hasattr(f, "close"):
                f.close()

    def reply(self, content, contentsize, contenttype, mtime=time.time()):
        # These are the four variables that must be set for a succesful request as it
        # is what is used to return content to the client, set headers, etc.        
        # If after analyzing the request it is still None
        # it means whatever was requested could not be found
        # Usually we just copy the returned output to the client
        # However if contentsize is chunked client will receive chunks until connections is closed
        func = copyfile
        if not content is None or not contentsize is None:
            self.send_response(200)
            self.send_header("Content-type", contenttype)
            self.send_header("Last-Modified", self.date_time_string(mtime))
            if contentsize == "chunked":
                self.send_header('Transfer-Encoding', 'chunked')
                func = chunkfile
            else:
                self.send_header("Content-Length", contentsize)

            self.end_headers()
        return func, content

    def send_head(self):
        # OBS project names are always of this form:
        #    projectname:gitreference:subdir
        threading.current_thread().name = self.path

        # Parse the client's data 
        urlparsed = urlparse.urlparse(self.path)
        urlpath = urlparsed[2]
        query = None
        
        if self.headers.getheader('Content-Length') is not None:
            data = self.rfile.read(int(self.headers.getheader('Content-Length')))
            query = urlparse.parse_qs(data)
        elif urlparsed[4] is not None:
            query = urlparse.parse_qs(urlparsed[4])
        else:
            query = {}

        # support both OBS remote link and direct osc
        if urlpath.startswith("/public"):
            urlpath = urlpath.replace("/public", "", 1)

        urlsplit = [ urllib.unquote(x) for x in urlpath.split("/")[2:]]
        # Begin handling the requests
        # This handles OBS API /public/source/*
        if urlpath.startswith("/source"):
            return self.handle_source(urlsplit, query)
        # entry point for binary build result repos OBS API /public/build/*
        elif urlpath.startswith("/build"):
            return self.handle_build(urlsplit, query)
        elif urlpath.startswith("/lastevents"):
            return self.handle_lastevents(urlsplit, query)
        elif urlpath.startswith("/update"):
            # private api for getting updates from upstream
            return self.handle_update(urlsplit, query)
        else:
            #unsupported
            raise RuntimeError("unsupported API %s" % self.path)

    def handle_source(self, sourcesplit, query):
        content = None
        contentsize = None
        contenttype = "text/xml"
        reply_kwargs  = {}

        # Fetch the project description (packages, other meta data, etc) dictionary for the indicated project
        project = gitmds2.get_project(sourcesplit[0])

        if project:

            if len(sourcesplit) == 1:
            # This handles:
            # /public/source/PROJECTNAME
            # - Basically build a XML output that states what packages are contained
            #   within the project
                contentsize, content = string2stream(gitmds2.build_project_index(project))

            elif len(sourcesplit) == 2:
            # /public/source/PROJECTNAME/_config
            # /public/source/PROJECTNAME/_meta
            # /public/source/PROJECTNAME/_pubkey
            # /public/source/PROJECTNAME/_pattern
            # /public/source/PROJECTNAME/PACKAGENAME
                # The project configuration, stored in our project dictionary
                if sourcesplit[1] == "_config":
                    contentsize, content = string2stream(project["prjconf"])
                    contenttype = "text/plain"

                # The project meta, stored in our project dictionary
                elif sourcesplit[1] == "_meta":
                    contentsize, content = string2stream(etree.tostring(project["meta"], pretty_print=True))

                #elif sourcesplit[1] == "_pubkey":
                #FIXME: We don't currently support extracting pubkeys
                #   content = None
                #elif sourcesplit[1] == "_pattern":
                #FIXME: We don't currently support extracting patterns
                #   content = None
                else:
                    expand = query.get("expand", None)
                    rev = query.get("rev", None)
                    # Determine if the remote OBS wants the expanded package
                    # for the linked package (we don't really support this, we just
                    # give packages different names)
                    if expand:
                        expand = int(expand[0])
                    # Determine what revision is being asked for of the package
                    if rev:
                        rev = rev[0]

                    # This will return a XML document containing the files of the package at the time of the revision
                    contentsize, content = string2stream(gitmds2.get_package_index(project, sourcesplit[1], rev))

            # /public/source/PROJECTNAME/PACKAGE/filename
            elif len(sourcesplit) == 3:
                expand = query.get("expand", None)
                rev = query.get("rev", None)
                # Determine if the remote OBS wants the expanded package
                # for the linked package (we don't really support this, we just
                # give packages different names)
                if expand:
                    expand = int(expand[0])
                # Determine what revision is being asked for of the package
                if rev:
                    rev = rev[0]
                contentsize, content = gitmds2.get_package_file(project, sourcesplit[1], sourcesplit[2], rev)
                contenttype = "application/octet-stream"
            #else:
            #   content = None
            if content is None or contentsize is None:
                self.send_error(404, "File not found %s" % os.path.join(*sourcesplit))
        else:
            log.info("404: %s" % os.path.join(*sourcesplit))
            self.send_error(404, "Project not found")

        return self.reply(content, contentsize, contenttype, **reply_kwargs)

    def handle_build(self, pathparts, query):
        content = None
        contentsize = None
        contenttype = "text/html"
        reply_kwargs  = {}

        #Mer:Trunk:Base/standard/i586/_repository?view=cache
        if len(pathparts) >= 3:
            prj_path = gitmds2.lookup_binariespath(pathparts[0])
            if prj_path:

                target = os.path.join(prj_path, pathparts[1], pathparts[2])
                if os.path.exists(target):

                    binary = query.get("binary", None)
                    if not isinstance(binary, list):
                        binary = []

                    view = query.get("view", None) 
                    if isinstance(view, list):
                        view = view[0]
                    else:
                        view = "names"

                    if view == "cache" or view == "solvstate":
                        if os.path.isfile(target + "/_repository?view=" + view):
                            contentsize, contentmtime, content = file2stream(target + "/_repository?view=" + view)
                        else:
                            contentsize, contentmtime, content = file2stream("tools/emptyrepositorycache.cpio")
                        reply_kwargs["mtime"] = contentmtime
                        contenttype = "application/octet-stream"

                    elif view == "cpio":
                        binaries = ""
                        for x in query["binary"]:
                            if not os.path.isfile(target + "/" + os.path.basename(x) + ".rpm"):
                                #FIXME: shouldn't an error be raised here
                                log.info(target + "/" + os.path.basename(x) + ".rpm was not found")
                            binaries = binaries + os.path.basename(x) + ".rpm\n"
                        cpiopipe = subprocess.Popen(["/usr/bin/cpio", "--quiet", "-o", "-H", "newc", "-C", "8192"], bufsize=8192, cwd=target, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
                        cpiopipe.stdin.write(binaries)
                        cpiopipe.stdin.close()
                        content = cpiopipe.stdout
                        contentsize = "chunked"
                        contenttype = "application/x-cpio"

                    elif view == "names":
                        if os.path.isfile(target + "/_repository?view=names"):
                            doc = etree.parse(target + "/_repository?view=names").getroot()
                            removables = []
                            for x in doc.iter("binary"):
                                if not os.path.splitext(x.attrib["filename"])[0] in binary:
                                    removables.append(x)
                            for x in removables:
                                doc.remove(x)
                            contentsize, content = string2stream(etree.tostring(doc, pretty_print=True))
                        else:
                            contentsize, content = string2stream("<binarylist />")
                    elif view == "binaryversions":
                        if os.path.isfile(target + "/_repository?view=cache"):
                            doc = etree.parse(target + "/_repository?view=binaryversions").getroot()
                            removables = []
                            for x in doc.iter("binary"):
                                if not os.path.splitext(x.attrib["name"])[0] in binary:
                                    removables.append(x)
                            for x in removables:
                                doc.remove(x)
                            contentsize, content = string2stream(etree.tostring(doc, pretty_print=True))
                        else:
                            contentsize, content = string2stream("<binaryversionlist />")

                    if content is None or contentsize is None:
                        self.send_error(404, "File not found %s" % os.path.join(*pathparts))

                else:
                    log.info("404: %s" % os.path.join(*pathparts))
                    self.send_error(404, "Repository not found")
            else:
                log.info("404: %s" % os.path.join(*pathparts))
                self.send_error(404, "Project not found")

        return self.reply(content, contentsize, contenttype, **reply_kwargs)

    def handle_lastevents(self, urlsplit, query):
        content = None
        contentsize = None
        contenttype = "text/html"
        reply_kwargs  = {}

        start = query.get("start", None)
        if start:
            start = int(start[0])

        qfilters = query.get("filter", [])
        filters = []
        obsname = query.get("obsname", "")
        threading.current_thread().name = "OBS Watcher %s" % obsname
        if start is None or start > gitmds2.get_next_event() :
            output = '<events next="' + str(gitmds2.get_next_event()) + '" sync="lost" />\n'
            contentsize, content = string2stream(output)

        elif not start is None and start == gitmds2.get_next_event():
            for x in qfilters:
                spl = x.split('/')
                if len(spl) == 2:
                    filters.append((urllib.unquote(spl[0]), urllib.unquote(spl[1]), None))
                else:
                    filters.append((urllib.unquote(spl[0]), urllib.unquote(spl[1]), urllib.unquote(spl[2])))

            log.info("%s: will poll every 2 seconds" % threading.current_thread().name)

            while start == gitmds2.get_next_event():
                time.sleep(2)
                #FIXME: also handle case when client disconnects, which so far seems not trivially possible
                if self.server._BaseServer__is_shut_down.is_set():
                    self.send_error(503, "Shutting down")
                    start = None

        if not start is None and start < gitmds2.get_next_event():
            contentsize, content = string2stream(gitmds2.get_events_filtered(start, filters))

        return self.reply(content, contentsize, contenttype, **reply_kwargs)

    def handle_update(self, urlsplit, query):
    # update/packages/Core
    # update/repo/Core/[latest|next|0.20121225.1]
        rsync_out = None
        content = None
        contentsize = None
        contenttype = "text/html"
        reply_kwargs  = {}
        if len(urlsplit) >= 2 and urlsplit[0] in ["packages", "repo"]:
            prjmap = gitmds2.get_mappings().xpath("//mapping[@project='%s']" % urlsplit[1])
            if prjmap:
                repos_path = prjmap[0].attrib.get("binaries", None)
                packages_path = prjmap[0].attrib.get("path", None)
                repos_upstream = prjmap[0].attrib.get("binaries-upstream", None)
                packages_upstream = prjmap[0].attrib.get("packages-upstream", None)

                if urlsplit[0] == "packages" and packages_upstream and packages_path:
                    rsync_out = subprocess.check_output(['rsync', '-vaHx', '--delete-after',
                                                        packages_upstream, packages_path])

                elif urlsplit[0] == "repo" and repos_upstream and repos_path:
                    repoid = "%s:*:%s" % (urlsplit[1], urlsplit[2])
                    repo_url = os.path.join(repos_upstream, repoid)
                    repo_path = os.path.join(repos_path, repoid)

                    rsync_out = subprocess.check_output(['rsync', '-vaHx', '--delete-after',
                                                    repo_url, repo_path])
                else:
                    log.info("404: %s" % os.path.join(*urlsplit))
                    self.send_error(404, "upstream not set in mappings file for this project")
            else:
                log.info("404: %s" % os.path.join(*urlsplit))
                self.send_error(404, "project not found")
        else:
            log.info("500: %s" % os.path.join(*urlsplit))
            self.send_error(500, "Request not understood")

        if rsync_out:
            refresh_cache()
            contentsize, content = string2stream(rsync_out)
        else:
            self.send_error(500, "Something went wrong")
            
        return self.reply(content, contentsize, contenttype, **reply_kwargs)

    
class MDSWebServer(SocketServer.ThreadingMixIn, BaseHTTPServer.HTTPServer):
    #daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 50

def refresh_cache():
    gitmds2.generate_mappings("mappingscache.xml", "lastevents.xml")

def warm_cache():
    _ = gitmds2.get_mappings()
    _ = gitmds2.get_mappingscache()
    _ = gitmds2.get_lastevents()
    log.info("Cache primed")

def terminate(http):
    http.shutdown()

def sighuphandler(signum, frame):
    log.info('Got a SIGHUP ...')
    refresh_cache()

def sigtermhandler(signum, frame):
    log.info('Got a SIGTERM ...')
    terminate(frame.f_locals["httpd"])

def sigusr1handler(signum, frame):
    log.info('Got a SIGUSR1 ...')
    log.info("\n".join(["%s %s" % (t.ident, t.name) for t in threading.enumerate()]))

def sigusr2handler(signum, frame):
    log.info('Got a SIGUSR2, dropping to debugger ...')
    log.info("Examine each thread's frame using traceback.print_stack on the list returned by sys._current_frames()")
    import pdb
    pdb.set_trace()

def parseArgs():
    parser = argparse.ArgumentParser(description='MDS2 usage.')
    parser.add_argument('port', metavar='PORT', type=int,
                   help='an integer for the http port to listen on')
    parser.add_argument('--debug', '-d', dest='debug', action='store_const', default=logging.INFO,
                   const=logging.DEBUG, help='run in debug and verbose log mode')
    parser.add_argument('--version', '-v', action='version', version='%(prog)s ' + __version__)
    return parser.parse_args()

if __name__ == "__main__":

    args = parseArgs()
    httpd = MDSWebServer(("0.0.0.0", args.port), MDSHTTPRequestHandler)
    log = logging.getLogger("mds2")
    log.setLevel(args.debug)

    try:
        # refresh caches
        refresh_cache()
        # preload some stuff
        warm_cache()
        # Start a thread with the server -- that thread will then start one
        # more thread for each request
        server_thread = threading.Thread(target=httpd.serve_forever)

        # Exit the server thread when the main thread terminates
        server_thread.daemon = True
        server_thread.name = "MDSWebServer"
        server_thread.start()

        # install signal handlers
        signal.signal(signal.SIGTERM, sigtermhandler)
        signal.signal(signal.SIGHUP, sighuphandler)
        signal.signal(signal.SIGUSR1, sigusr1handler)
        signal.signal(signal.SIGUSR2, sigusr2handler)

        log.info("%s thread running with pid %s" % (server_thread.name, os.getpid()))
        while server_thread.is_alive():
            time.sleep(2)

    except KeyboardInterrupt:
        log.info("Shutdown requested ...")
        terminate(httpd)

    log.info("Shutdown complete.")
    logging.shutdown()
    sys.exit(0)

