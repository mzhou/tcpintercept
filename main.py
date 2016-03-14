#!/usr/bin/python -ttu

# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4

import collections
import errno
import functools
import select
import socket
import sys

import deque_buffer
import util

Config = collections.namedtuple('Config', (
    'listen_ip',
    'listen_port',
    'bind_ip',
    'bind_port'
    ))

class CallbackPoll(object):
    def __init__(self):
        super(CallbackPoll, self).__init__()
        self._p = select.poll()
        self._cbs = {}

    def register(self, fd, cb, *args, **kwargs):
        fd = self._extract_fd(fd)
        self._cbs[fd] = cb
        try:
            return self._p.register(fd, *args, **kwargs)
        except:
            del self._cbs[fd]
            raise

    def modify(self, *args, **kwargs):
        return self._p.modify(*args, **kwargs)

    def unregister(self, fd, *args, **kwargs):
        fd = self._extract_fd(fd)
        del self._cbs[fd]
        return self._p.unregister(fd)

    def poll(self, *args, **kwargs):
        for fd, event in self._p.poll(*args, **kwargs):
            self._cbs.get(fd, self._default_callback)(fd, event)

    @staticmethod
    def _extract_fd(o):
        try:
            return o.fileno()
        except:
            return int(o)

    @staticmethod
    def _default_callback(fd, event):
        pass

class Connection(object):
    # we take ownership of ls
    def __init__(self, cbp, bind, ls, src, dst):
        self._cbp = cbp
        self._bind = bind
        self._ls = ls
        self._src = src
        self._dst = dst
        self._rs = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._to_remote_buf = deque_buffer.DequeBuffer()
        self._to_local_buf = deque_buffer.DequeBuffer()
        self._local_disconnected = False
        self._remote_disconnected = False
        self._local_writable = True
        self._remote_writable = True
        self._to_remote_peak = 0
        self._to_local_peak = 0

    def begin_connect(self):
        self._cbp.register(self._rs, self.connect_callback)
        self._rs.setblocking(0)
        err = self._rs.connect_ex(self._dst)
        if err not in (0, errno.EINPROGRESS):
            self._connect_failed()

    def connect_callback(self, fd, event):
        assert fd == self._rs.fileno()
        assert event & select.POLLOUT
        err = self._rs.connect_ex(self._dst)
        if err not in (0, errno.EINPROGRESS):
            self._connect_failed()
            return
        # start caring about the local socket
        self._ls.setblocking(0)
        self._cbp.register(self._ls, self.local_poll_callback)
        # change the callback for the remote socket
        self._cbp.register(self._rs, self.remote_poll_callback)

    def _connect_failed(self):
        self._cbp.unregister(self._rs)
        self._ls.close()

    def local_poll_callback(self, fd, event):
        assert fd == self._ls.fileno()
        if event & select.POLLOUT:
            if self._to_local_buf:
                d = self._to_local_buf.popleftall()
                sent, self._local_disconnected = util.send_until_block(self._ls, d)
                if self._local_disconnected:
                    self._to_local_buf.clear()
                elif sent < len(d):
                    self._to_local_buf.appendleft(d[sent:])
        if event & select.POLLIN:
            buffers, self._local_disconnected = util.recv_until_block(self._ls, 1*1024*1024)
            self._to_remote_buf.extend(buffers)
        self._update_peaks()
        self._check_writable()
        self._check_cleanup()

    def remote_poll_callback(self, fd, event):
        assert fd == self._rs.fileno()
        if event & select.POLLOUT:
            if self._to_remote_buf:
                d = self._to_remote_buf.popleftall()
                sent, self._remote_disconnected = util.send_until_block(self._rs, d)
                if self._remote_disconnected:
                    self._to_remote_buf.clear()
                elif sent < len(d):
                    self._to_remote_buf.appendleft(d[sent:])
        if event & select.POLLIN:
            buffers, self._remote_disconnected = util.recv_until_block(self._rs, 1*1024*1024)
            self._to_local_buf.extend(buffers)
        self._update_peaks()
        self._check_writable()
        self._check_cleanup()

    def _update_peaks(self):
        if len(self._to_remote_buf) > self._to_remote_peak:
            print '{}:{}->{}:{} Peak={}'.format(
                self._src[0],
                self._src[1],
                self._dst[0],
                self._dst[1],
                len(self._to_remote_buf))
            self._to_remote_peak = len(self._to_remote_buf)
        if len(self._to_local_buf) > self._to_local_peak:
            print '{}:{}->{}:{} Peak={}'.format(
                self._dst[0],
                self._dst[1],
                self._src[0],
                self._src[1],
                len(self._to_local_buf))
            self._to_local_peak = len(self._to_local_buf)

    def _check_writable(self):
        if self._to_remote_buf and not self._remote_writable:
            self._cbp.modify(self._rs, select.POLLIN | select.POLLOUT)
            self._remote_writable = True
        if self._to_local_buf and not self._local_writable:
            self._cbp.modify(self._ls, select.POLLIN | select.POLLOUT)
            self._local_writable = True

        if not self._to_remote_buf and self._remote_writable:
            self._cbp.modify(self._rs, select.POLLIN)
            self._remote_writable = False
        if not self._to_local_buf and self._local_writable:
            self._cbp.modify(self._ls, select.POLLIN)
            self._local_writable = False

    def _check_cleanup(self):
        if ((self._remote_disconnected and not self._to_local_buf)
                or (self._local_disconnected and not self._to_remote_buf)):
            self._disconnected()

    def _disconnected(self):
        self._cbp.unregister(self._rs)
        self._cbp.unregister(self._ls)
        self._rs.close()
        self._ls.close()

class Listener(object):
    def __init__(self, cbp, connection_factory, banned_port, ss):
        super(Listener, self).__init__()
        self._cbp = cbp
        self._connection_factory = connection_factory
        self._banned_port = banned_port
        self._ss = ss
        self._cbp.register(ss, self.poll_callback)

    def poll_callback(self, fd, event):
        assert fd == self._ss.fileno()
        assert event & select.POLLIN
        cs, src = self._ss.accept()
        dst = util.get_original_dst(cs)
        if dst[1] == self._banned_port:
            # probable direct connection to the proxy
            cs.close()
        else:
            conn = self._connection_factory(cs, src, dst)
            conn.begin_connect()

def main(argv):
    config = Config(
        argv[1],
        int(argv[2]),
        argv[3] if len(argv) >= 4 else None,
        argv[4] if len(argv) >= 5 else None,
    )

    cbp = CallbackPoll()
    bind = None
    if config.bind_ip or config.bind_port:
        bind = (config.bind_ip, config.bind_port)
    connection_factory = functools.partial(Connection, cbp, bind)

    # incoming socket
    ss = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ss.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    ss.bind((config.listen_ip, config.listen_port))
    ss.listen(128)
    l = Listener(cbp, connection_factory, config.listen_port, ss)

    while True:
        cbp.poll()

    return 0

if __name__ == '__main__':
    sys.exit(main(sys.argv))
