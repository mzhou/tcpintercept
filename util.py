# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4

import errno
import socket
import struct

def get_original_dst(s):
    # https://mail.python.org/pipermail/python-list/2010-May/576780.html
    dst_port, dst_ip = struct.unpack('!2xH4s8x', s.getsockopt(socket.SOL_IP, 80, 16))
    return (socket.inet_ntoa(dst_ip), dst_port)

def recv_until_block(s, bufsize):
    buffers = []
    total_len = 0
    disconnected = False
    while True:
        try:
            d = s.recv(bufsize)
            if not d:
                disconnected = True
                break
            buffers.append(d)
            total_len += len(d)
        except socket.error, e:
            if e[0] != errno.EWOULDBLOCK:
                disconnected = True
            break
    return (buffers, disconnected, total_len)

def send_until_block(s, d):
    sent = 0
    disconnected = False
    while sent < len(d):
        try:
            if sent == 0:
                this_sent = s.send(d)
            else:
                this_sent = s.send(d[sent:])
            if this_sent <= 0:
                break
            sent += this_sent
        except socket.error, e:
            if e[0] != errno.EWOULDBLOCK:
                disconnected = True
                break
            break
    return (sent, disconnected)
