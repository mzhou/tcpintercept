# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4

import socket
import struct

def get_original_dst(s):
    # https://mail.python.org/pipermail/python-list/2010-May/576780.html
    dst_port, dst_ip = struct.unpack('!2xH4s8x', s.getsockopt(socket.SOL_IP, 80, 16))
    return (socket.inet_ntoa(dst_ip), dst_port)
