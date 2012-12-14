__version__ = "2.0"

import os, sys
#import posixpath
import SocketServer
import BaseHTTPServer
#import urllib
#import cgi
import time
import shutil
#import mimetypes
import urlparse
#import uuid
import gitmds2
#import subprocess
#import xml.dom.minidom
import traceback
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

#
# MDSHTTPRequestHandler handles the incoming HTTP requests
# The basic flow is that GET/POST/HEAD attempts to 
# run the send_head command, and if that throws an exception,
# a 500 will be sent
# 
# send_head returns a content stream, or None if things fail

class MDSHTTPRequestHandler(BaseHTTPServer.BaseHTTPRequestHandler):
    server_version = "mds/" + __version__

    def do_GET(self):
        """Serve a GET request."""
        f = None
        try:
            f = self.send_head()
        except: 
            self.send_response(500)
            print "500: " + self.path
            traceback.print_exc(file=sys.stdout)
            self.end_headers()
        if f:
            self.copyfile(f, self.wfile)
            if hasattr(f, "close"):
               f.close()

    def do_HEAD(self):
        """Serve a HEAD request."""
        f = None
        try:
            f = self.send_head()
        except:
        
            if f:
              if hasattr(f, "close"):
                f.close()

    def do_POST(self):
        f = None
        try:
          f = self.send_head()
        except:
          self.send_response(500)
          print "500: " + self.path
          traceback.print_exc(file=sys.stdout)
          self.end_headers()
        if f:
          self.copyfile(f, self.wfile)
          if hasattr(f, "close"):
            f.close()             
        

    def send_head(self):
        # OBS project names are always of this form:
        #    projectname:gitreference:subdir
        
        # Handy helper:
        #  This converts a string into a stream and returns the size and the content stream
        def string2stream(thestr):
            content = StringIO()
            content.write(thestr)
            content.seek(0, os.SEEK_END)
            contentsize = content.tell()
            content.seek(0, os.SEEK_SET)
            return contentsize, content

        # These are the four variables that must be set for a succesful request as it
        # is what is used to return content to the client, set headers, etc.        
        content = None
        contentsize = 0
        contentmtime = 0
        contenttype = None
        
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
           
        # Begin handling the requests
        # This handles OBS API /public/source/*
        if urlpath.startswith("/public/source/"):
           urlpathsplit = urlpath.split("/") 
           sourcesplit = urlpathsplit[3:]
           
           # Fetch the project description (packages, other meta data, etc) dictionary for the indicated project
           project = gitmds2.get_project(sourcesplit[0])
           if not project is None:
              if len(sourcesplit) == 1:
              # This handles:
              # /public/source/PROJECTNAME
              # - Basically build a XML output that states what packages are contained
              #   within the project
                 contentsize, content = string2stream(gitmds2.build_project_index(project))
                 contenttype = "text/xml"
                 contentmtime = time.time()
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
                    contentmtime = time.time()
                 # The project meta, stored in our project dictionary
                 elif sourcesplit[1] == "_meta":
                    contentsize, content = string2stream(etree.tostring(project["meta"], pretty_print=True))
                    contenttype = "text/xml"
                    contentmtime = time.time()
                 elif sourcesplit[1] == "_pubkey":
                 # We don't currently support extracting pubkeys
                    content = None
                 elif sourcesplit[1] == "_pattern":
                 # We don't currently support extracting patterns
                    content = None
                 else:
                    expand = 0
                    rev = None
                    # Determine if the remote OBS wants the expanded package
                    # for the linked package (we don't really support this, we just
                    # give packages different names)
                    if query.has_key("expand"):
                        expand = int(query["expand"][0])
                    # Determine what revision is being asked for of the package
                    if query.has_key("rev"):
                        rev = query["rev"][0]
                    
                    # This will return a XML document containing the files of the package at the time of the revision
                    contentsize, content = string2stream(gitmds2.get_package_index(project, sourcesplit[1], rev))
                    contenttype = "text/xml"
                    contentmtime = time.time()
                 
              elif len(sourcesplit) == 3:
                rev = None
                expand = 0
                # Determine if the remote OBS wants the expanded package
                # for the linked package (we don't really support this, we just
                # give packages different names)
                if query.has_key("expand"):
                        expand = int(query["expand"][0])
                # Determine what revision is being asked for of the package
                if query.has_key("rev"):
                        rev = query["rev"][0]
                contentsize, contentst = gitmds2.get_package_file(project, sourcesplit[1], sourcesplit[2], rev)
                # Drop contentz, we already know this from contentsize
                contentz, content = string2stream(contentst)
                contenttype = "application/octet-stream"
                contentmtime = time.time()
              # /public/source/PROJECTNAME/PACKAGE/filename
              else:
                 content = None
        else:
            content = None
        
        if content is None:
              print "404: path"
              self.send_error(404, "File not found")
              return None
              
        self.send_response(200)
        self.send_header("Content-type", contenttype)
        self.send_header("Content-Length", contentsize)
        self.send_header("Last-Modified", self.date_time_string(contentmtime))
        self.end_headers()
        return content
        
    def copyfile(self, source, outputfile):
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
    
    
class MDSWebServer(SocketServer.ThreadingMixIn, BaseHTTPServer.HTTPServer):
        pass

# Set up a one-thread-per-request http server on the port indicated in sys.argv[1]
PORT = int(sys.argv[1])

httpd = MDSWebServer(("0.0.0.0", PORT), MDSHTTPRequestHandler)
httpd.serve_forever()

  
