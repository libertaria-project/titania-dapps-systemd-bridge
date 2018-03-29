#!/usr/bin/env python3
from fusepy import FUSE, FuseOSError, Operations
import sys, errno, os, re, stat
import json

# Don't try to prononce the name of the class
# I'm not liable for the daemons you might 
# summon if you do.
class PydAppHubFuse(Operations):
    def __init__(self, jsonroot):
        self.jsonroot = jsonroot

        # Config file cache
        self.cache = {}

        # Ensuring after open() the result doesn't change
        self.filecache = {}
        # TODO: rely on the set of keys of above instead
        self.lastfd = -1

        # dApp database collected from JSONs 
        # TODO error if file not found etc
        with open(jsonroot) as fp:
            jsonobj = json.load(fp)

        self.dapps = { v['name']:v for v in jsonobj }

    # Classifies the incoming path value:
    #  - None = invalid path
    #  - True = valid directory
    #  - dapp name = valid dapp.conf, corresponding entry is returned
    def classify(self, path):
        # Root is a valid directory
        if path == '/':
            return True
        
        # Check if the directory corresponds to dapp pattern
        # TODO: what are valid dapp names and how they are named anyway?
        m = re.match("^/dapp@([A-Za-z-_]+).service.d(/.*)?$", path)
        if not m:
            return None
        dapp = m.group(1)

        # Check if we are dealing with a directory
        fname = m.group(2)
        if not fname:
            return True

        # Check if we have that dapp
        if dapp not in self.dapps:
            return None

        # Find the entry if the filename is correct
        if fname == '/dapp.conf':
            return dapp 

        return None

    # Same as above but automatically raises ENOENT
    def getobj(self, path):
        obj = self.classify(path)

        # Cut what's not ours
        if not obj:
            raise FuseOSError(errno.ENOENT)

        return obj

    # Generate config data
    def genconfig(self, dapp):
        d = self.dapps[dapp]

        conf = '''#
# Automatically generated by FUSE from dApp Hub JSON
# Do not [attempt] to edit
#
[Unit]
Description={}
'''.format(d['description'])
        
        # Port forwarding setup
        ports = ('Wants=forward-port@{port}-{protocol}.service'.format(**port) for port in d['ports'] if port['type']=='public')
        conf += '\n'.join(ports)

        conf += '\n\n'

        conf += '[Service]\n'

        # Port publishing setup
        # TODO: should we specify tcp/udp things?
        conf += 'Environment=DAPP_DOCKER_PORTS="%s"' % ' '.join('-p{port}/{protocol}'.format(**port) for port in d['ports'])

        # Environment setup
        #env = ('Environment={}={}'.format(env, val['value']) for env,val in d['env'].items())
        #conf += '\n'.join(env)

        # Providing image name and filename for dapp@.service to use
        conf += '\n# Making sure we overwrite previous values\n'
        conf += 'Environment=DAPP_DOCKER_IMAGE=%s\n' % d['image']
        # Being explicit here so that we don't have to wrap in a shell script
        conf += 'Environment=DAPP_DOCKER_IMAGE_FILE=/var/lib/docker/preinstall/%s.tar\n' % d['image'].replace('/','_').replace(':','_')         
        conf += '\n'

        return conf

    # Get config file data for dapp
    def getconfig(self, dapp):
        # Check cache
        if dapp not in self.cache:
            self.cache[dapp] = self.genconfig(dapp)
        # Okay we need to generate
        return self.cache[dapp]

    def access(self, path, mode):
        obj = self.getobj(path)

        # Allow read/execute on / and valid dirs
        # read only on anything else
        isdir = type(obj) is not str
        if (mode & os.W_OK) or (not isdir and mode & os.X_OK):
            raise FuseOSError(errno.EACCES)

    # TODO: support reading by handle?

    def getattr(self, path, fh):
        obj = self.getobj(path)
        isdir = type(obj) is not str

        # TODO: meaningful values
        st_mode = (stat.S_IFDIR | 0o755) if isdir else (stat.S_IFREG | 0o644)
        st_size = 0 if isdir else len(self.getconfig(obj))
        res = { 
            'st_atime': 0,
            'st_ctime': 0,
            'st_gid':   0,  
            'st_mode':  st_mode,
            'st_mtime': 0,
            'st_nlink': 0,
            'st_size':  st_size,
            'st_uid':   0
        }

        return res

    def readdir(self, path, fh):
        obj = self.getobj(path)
        # Can't list files
        if type(obj) is dict:
            raise FuseOSError(errno.EBADF)
        dirents = ['.', '..']
        # Root lists valid units
        if path == '/':
            dirents.extend('dapp@%s.service.d' % dapp for dapp in self.dapps)
        # Else only one file
        else:
            dirents.append('dapp.conf')

        # Sufficiently recent python required
        yield from dirents

    # TODO: statfs
    # TODO: what if path doesn't mach definition?
    def open(self, path, flags):
        obj = self.getobj(path)
        # if type(obj) is not str: TODO then what???
        # TODO: not multithreading friendly
        self.lastfd += 1
        self.filecache[self.lastfd] = self.getconfig(obj)
        return self.lastfd

    # TODO: here and below invalid descriptor error
    def release(self, path, fh):
        del self.filecache[self.lastfd]

    def read(self, path, length, offset, fh):
        doc = self.filecache[fh]
        end = offset + min(len(doc) - offset, length)
        return doc[offset:end].encode('ascii')

    # Silently nod when asked to sync instead of ENOSYS error
    def fsync(self, *args, **kwargs):
        pass

# TODO: re-work in order to make systemd friendly

# Standalone operation
if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("Usage: ./pydapphubfuse.py /path/to/json /mount/point")
    # TODO: study if we need nothreads here
    else:
        driver = PydAppHubFuse(sys.argv[1])
        # Uncomment for debugging
        indent = 0
        for m in dir(driver):
            fun = getattr(driver,m) 
            if m[0]!='_' and type(fun) is type(driver.fsync):
                def trace_method(fun, name):
                    def f(*args, **kwargs):
                        global indent
                        print("{}[TRACE]: {}({},{}) ".format('\t'*indent, name, args, kwargs))
                        indent += 1
                        try:
                            res = fun(*args, **kwargs)
                        except:
                            indent -= 1
                            print("{}exception".format('\t'*indent))
                            raise
                        indent -= 1
                        print("{}return {}".format('\t'*indent,res))
                        return res
                    return f
                setattr(driver, m, trace_method(fun, m))

        FUSE(driver, sys.argv[2], nothreads=True, foreground=True)