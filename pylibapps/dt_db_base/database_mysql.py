import os
import sys
import time
import pymysql
from datetime import timezone


from .database import tester_database
from .db_inf import db_inf, db_cursor

_MYSQL_AUTO_DISCONNECT = 60 * 5


class mysql_db_cursor(db_cursor):
    def __init__(self, parent):
        db_cursor.__init__(self, parent)

    def insert(self, cmd, params=()):
        self._execute(cmd, params)
        ret = self._c.lastrowid
        return ret


def _do_raw_connect(db_def):
    ssl = {}
    for ssl_info in ["ssl_ca",
                     "ssl_check_hostname",
                     "ssl_verify_mode",
                     "ssl_cert",
                     "ssl_key",
                     "ssl_password"]:
        val = db_def.get(ssl_info)
        if val:
            ssl[ssl_info[4:]] = val
    if len(ssl) == 0:
        ssl = None
    return pymysql.connect(database=db_def["dbname"],
                           user=db_def["user"],
                           password=db_def["password"],
                           host=db_def["host"],
                           port=db_def.get("port", 3306),
                           ssl=ssl,
                           sql_mode='ANSI_QUOTES',
                           connect_timeout=10)


class mysql_db_inf(db_inf):
    def __init__(self, db_def):
        db_inf.__init__(self,
                        db_def,
                        _do_raw_connect,
                        _MYSQL_AUTO_DISCONNECT)

    def cursor(self):
        return mysql_db_cursor(self)


class mysql_tester_database(tester_database):
    def get_db_now():
        row = self.db.query_one("SELECT NOW()")
        return row[0].astimezone(timezone.utc)


class mysql_db_backend(object):
    def __init__(self, db_def):
        self.db_def = db_def

    def open(self, work_folder):
        return mysql_tester_database(mysql_db_inf(self.db_def),
                                     self.db_def['sql'],
                                     work_folder,
                                     self.db_def)

    def is_empty(self):
        db = _do_raw_connect(self.db_def)
        c = db.cursor()
        cmd = "SELECT table_name FROM information_schema.tables WHERE table_schema = '" + self.db_def["dbname"] + "'"
        c.execute(cmd)
        rows = c.fetchall()
        return not len(rows)

    def load(self, schema):
        db = _do_raw_connect(self.db_def)

        c = db.cursor()

        for s in schema:
            s = s.strip()
            if len(s):
                s = s.replace("AUTOINCREMENT", "AUTO_INCREMENT")
                s = s.replace("BEGIN TRANSACTION", "START TRANSACTION")
                try:
                    c.execute(s)
                except Exception as e:
                    raise Exception('Failed to do SQL "%s" : %s' % (s, str(e)))

        db.commit()
        db.close()
