# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4

import collections

class DequeBuffer(object):
    def __init__(self):
        self._q = collections.deque()
        self._len = 0

    def append(self, o):
        self._len += len(o)
        return self._q.append(o)

    def appendleft(self, o):
        self._len += len(o)
        return self._q.appendleft(o)

    def popleft(self, n):
        ret = []
        so_far = 0
        while so_far < n and self._q:
            to_go = n - so_far
            front = self._q.popleft()
            if len(front) <= to_go:
                ret.append(front)
                so_far += len(front)
            else:
                ret.append(front[:to_go])
                self._q.appendleft(front[to_go:])
                so_far += to_go
        self._len -= so_far
        return ''.join(ret)

    def popleftall(self):
        return self.popleft(len(self))

    def __len__(self):
        return self._len
