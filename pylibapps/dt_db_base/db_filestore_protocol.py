import os
import time
import shutil
import hashlib
from shutil import copyfile


def get_hash_folders(filename):
    hash_md5 = hashlib.md5()
    hash_md5.update(filename)
    h = hash_md5.hexdigest()
    return [ h[n:n+2] for n in range(0, 8, 2) ]


def get_batch_folders(file_id):
    r = ["files"]
    while file_id > 1000:
        file_id = int(file_id / 1000)
        r += ["_%0003u_" % (file_id % 1000)]
    r.reverse()
    return r


class smb_transferer(object):
    protocol_id=2
    def __init__(self):
        self._ctx = None
        self._host = None
        self._base_folder = None
        self._cache_con = {}

    def init(self, file_store_host, file_store_folder):
        pass

    def _get_smb_username(self):
        return None

    def _get_smb_password(self):
        return None

    def _get_smb_domain(self):
        return None

    def _do_auth(self, svr, shr, wg, un, pw):
        r = (self._get_smb_domain(),
                self._get_smb_username(),
                self._get_smb_password())
        return r

    def open(self, file_store_host, file_store_folder):
        cache_key = (file_store_host, file_store_folder)
        cache_entry = self._cache_con.get(cache_key, None)

        if cache_entry:
            now = time.time()
            if cache_entry[1] - now < 60 * 5:
                cache_entry[1] = now
                self._ctx = cache_entry[0]
                self._host = file_store_host
                self._base_folder = file_store_folder
                return
            else:
                self._cache_con.pop(cache_key)

        import smbc
        self._ctx = smbc.Context(auth_fn=self._do_auth)
        self._host = file_store_host
        self._base_folder = file_store_folder

        self._cache_con[cache_key] = [self._ctx, time.time()]

    def clean(self):
        self._ctx = None
        self._host = None
        self._base_folder = None
        self._cache_con = {}

    def _safe_name(self, filename):
        filename = "".join(
            map(lambda x: "_" if x in ':*/\?<>"|' else x, filename))
        return urllib.pathname2url(filename) # Spaces are safe like this

    def upload(self, filepath, file_id):
        filename = os.path.basename(filepath)
        remote_uri = "smb://%s/%s/%i.%s" % (self._host,
                                            self._base_folder,
                                            file_id,
                                            self._safe_name(filename))
        f = self._ctx.open(remote_uri, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o0644)

        with open(filepath) as f2:
            shutil.copyfileobj(f2, f)

    def download(self, filepath, file_id, mod_time):
        filename = os.path.basename(filepath)
        remote_uri = "smb://%s/%s/%i.%s" % (self._host,
                                            self._base_folder,
                                            file_id,
                                            self._safe_name(filename))
        f = self._ctx.open(remote_uri, os.O_RDONLY)

        with open(filepath, "w") as f2:
            shutil.copyfileobj(f, f2)

        os.utime(filepath, (mod_time, mod_time))


class sftp_connection(object):
    def __init__(self, file_store_host, db_def):
        import paramiko
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        port = 22
        split_pos = file_store_host.find(':')
        if split_pos != -1:
            port = int(file_store_host[split_pos+1:])
            file_store_host = file_store_host[:split_pos]

        ssh.connect(file_store_host,
                    username=db_def.get("sftp_user",None),
                    password=db_def.get("sftp_password",None),
                    port=port)
        self.ssh = ssh
        self.sftp_con = ssh.open_sftp()

    def put(self, filepath, remote_file):
        self.sftp_con.put(filepath, remote_file)

    def get(self, remote_file, filepath):
        self.sftp_con.get(remote_file, filepath)

    def exists(self, path):
        try:
            self.sftp_con.stat(path)
            return True
        except:
            return False

    def mkdir(self, path):
        self.sftp_con.mkdir(path)

    def close(self):
        self.sftp_con.close()
        self.ssh.close()


class local_connection(object):

    def put(self, filepath, remote_file):
        shutil.copy(filepath, remote_file)

    def get(self, remote_file, filepath):
        shutil.copy(remote_file, filepath)

    def exists(self, path):
        return os.path.exists(path)

    def mkdir(self, path):
        os.mkdir(path)

    def close(self):
        pass


class sftp_transferer(object):
    protocol_id=1
    def __init__(self, db_def={}):
        self._db_def = db_def
        self._con = None
        self._base_folder = None
        self._cache_con = {}

    def init(self, file_store_host, file_store_folder):
        if file_store_host.lower() == "localhost":
            con = local_connection()
            if not con.exists(file_store_folder):
                con.mkdir(file_store_folder)

    def open(self, file_store_host, file_store_folder):
        cache_key = (file_store_host, file_store_folder)
        cache_entry = self._cache_con.get(cache_key, None)

        if cache_entry:
            now = time.time()
            if cache_entry[1] - now < 60 * 5:
                cache_entry[1] = now
                self._con = cache_entry[0]
                self._base_folder = file_store_folder
                return
            else:
                self._cache_con.pop(cache_key)
                cache_entry[0].close()

        self._base_folder = file_store_folder

        if file_store_host.lower() == "localhost":
            self._con = local_connection()
        else:
            self._con = sftp_connection(file_store_host, self._db_def)
        self._cache_con[cache_key] = [self._con, time.time()]

    def _get_remote_name(self, filepath, file_id, upload=False, schema=2):
        filename = os.path.basename(filepath)
        remote_filename = "%i.%s" % (file_id, filename)
        if schema == 2:
            folders = get_batch_folders(file_id)
        elif schema == 1:
            folders = get_hash_folders(remote_filename)
        elif schema == 0:
            filename = os.path.basename(filepath)
            return os.path.join(self._base_folder, remote_filename)
        else:
            raise Exception("Unknown file path schema.")
        path = self._base_folder
        for folder in folders:
            path = os.path.join(path, folder)
            if not self._con.exists(path):
                if upload:
                    self._con.mkdir(path)
        remote_filepath = os.path.join(path, remote_filename)
        return remote_filepath

    def clean(self):
        self._con = None
        self._base_folder = None
        for entry in self._cache_con.values():
            entry[0].close()
        self._cache_con = {}

    def upload(self, filepath, file_id):
        remote_filepath = self._get_remote_name(filepath, file_id, True)
        self._con.put(filepath, remote_filepath)

    def download(self, filepath, file_id, mod_time):
        # Try remote paths, newest schema to oldest.
        remote_filepath = self._get_remote_name(filepath, file_id)
        if not self._con.exists(remote_filepath):
            remote_filepath = self._get_remote_name(filepath, file_id, schema=1)
            if not self._con.exists(remote_filepath):
                remote_filepath = self._get_remote_name(filepath, file_id, schema=0)

        self._con.get(remote_filepath, filepath)
        os.utime(filepath, (mod_time, mod_time))
