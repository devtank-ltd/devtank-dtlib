import time
import os

from . import int_logging

_logger = int_logging.get_logger(__name__)

_db_debug_print = _logger.debug


def set_debug_print(cb):
    global _db_debug_print
    _db_debug_print = cb


class db_cursor(object):
    def __init__(self, parent):
        self._parent = parent
        try:
            self._c      = parent._get_db().cursor()
        except Exception as e:
            self._parent.fail_catch(e)

    def _execute(self, cmd, params=()):
        self._parent._last_used = time.time()
        try:
            self._c.execute(cmd, params)
            _db_debug_print("SQL : '%s' (%s)" % (cmd, str(params)))
        except Exception as e:
            _logger.error('SQL "%s" (%s) failed' % (cmd, str(params)))
            self._parent.fail_catch(e)

    def query(self, cmd, params=()):
        self._execute(cmd, params=params)
        try:
            return self._c.fetchall()
        except Exception as e:
            self._parent.fail_catch(e)

    def query_one(self, cmd, params=()):
        self._execute(cmd, params=params)
        try:
            return self._c.fetchone()
        except Exception as e:
            self._parent.fail_catch(e)

    def update(self, cmd, params=()):
        self._execute(cmd, params=params)

    def insert(self, cmd, params=()):
        self._execute(cmd, params=params)
        return self._c.lastrowid


class db_inf(object):
    def __init__(self, db_def, connect_fn, disconnect_time=5 * 60):
        self.db_def = db_def
        self._db = None
        self._current = None
        self._cur_count = 0
        self._last_used = time.time()
        self._disconnect_time = db_def.get("disconnect_time", disconnect_time)
        self._connect_fn = connect_fn
        self.error_handler = None

    def _get_db(self):
        if not self._db:
            self.wake()
        return self._db

    def cursor(self):
        if self._current:
            return self._current
        return db_cursor(self)

    def commit(self):
        if not self._current:
            try:
                self._get_db().commit()
            except Exception as e:
                self._parent.fail_catch(e)

    def rollback(self):
        try:
            self._get_db().rollback()
        except Exception as e:
            self._parent.fail_catch(e)

    def query(self, cmd, params=()):
        return self.cursor().query(cmd, params=params)

    def query_one(self, cmd, params=()):
        return self.cursor().query_one(cmd, params=params)

    def update(self, cmd, params=()):
        c = self.cursor()
        c.update(cmd, params)
        if self._current is None:
            self.commit()

    def insert(self, cmd, params=()):
        c = self.cursor()
        r = c.insert(cmd, params)
        if self._current is None:
            self.commit()
        return r

    def __enter__(self):
        if not self._current:
            self._current = self.cursor()
        self._cur_count += 1
        self._last_used = time.time()
        return self._current

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._cur_count -= 1
        if not self._cur_count:
            self._current = None
            self._last_used = time.time()

    def fail_catch(self, e):
        if self._db:
            try:
                self._db.close()
            except:
                import traceback
                traceback.print_exc()
        self._db = None
        self._current = None
        self._cur_count = 0
        _logger.error("Bad DB : " + self.db_def['type'])
        if self.error_handler:
            self.error_handler(e)

    def wake(self):
        try:
            self._db = self._connect_fn(self.db_def)
            self._last_used = time.time()
            _logger.info("Connected DB : " + self.db_def['type'])
        except Exception as e:
            self.fail_catch(e)

    def clean(self):
        if not bool(self._current):
            delta = time.time() - self._last_used
            if delta > self._disconnect_time:
                if self._db:
                    _logger.info("Auto disconnect DB")
                    self._db.close()
                    self._db = None
