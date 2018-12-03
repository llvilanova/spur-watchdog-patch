#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import print_function
from __future__ import absolute_import


import atexit
import collections
import logging
import os
import signal
import spur
import sys
import threading
import traceback
import time


logger = logging.getLogger(__name__)


# Patch spur to raise SIGINT while waiting for a process (python 2 only)

if sys.version_info[0] < 3:
    def _wait_for_result_patch(self):
        # Python will raise SIGINT between iterations, but not when blocked on
        # the wait used by _generate_result()
        while self.is_running():
            time.sleep(.1)
        if self._result is None:
            self._result = self._generate_result()
        return self._result
    import spur.ssh
    spur.ssh.SshProcess.wait_for_result = _wait_for_result_patch
    import spur.local
    spur.local.LocalProcess.wait_for_result = _wait_for_result_patch


# Patch spur to update the _is_killed attribute when sending signals
def _patch_send_signal(func):
    def send_signal_wrapper(self, signum):
        if signum in [signal.SIGINT, signal.SIGQUIT, signal.SIGKILL]:
            self._is_killed = True
        return func(self, signum)
    return send_signal_wrapper
import spur.ssh
spur.ssh.SshProcess.send_signal = _patch_send_signal(spur.ssh.SshProcess.send_signal)
import spur.local
spur.local.LocalProcess.send_signal = _patch_send_signal(spur.local.LocalProcess.send_signal)


# Monitor background processes for failures, so we can error out early

_EXITING = False
_LOCK = threading.RLock()

def _watchdog_thread(shell, obj, cmd_msg, exit_on_error):
    stack_info = traceback.extract_stack()
    def watchdog():
        try:
            obj.wait_for_result()
        except Exception as e:
            _LOCK.acquire()
            if not obj._is_killed and not _EXITING:
                stack_idx = 0 if stack_info[0][2] == "<module>" else 6
                print("Traceback (most recent call last):")
                msg = traceback.format_list(stack_info[stack_idx:-1])
                print("".join(msg), end="")
                exc_type, exc_value, tb = sys.exc_info()
                info = traceback.extract_tb(tb)
                msg = traceback.format_list(info)
                print("".join(msg), end="")
                print("%s.%s: %s" % (exc_type.__module__, exc_type.__name__, exc_value))
                print("command:", cmd_msg)
                if exit_on_error:
                    _LOCK.release()
                    os._exit(1)
            shell._child_remove(obj)
            _LOCK.release()
        logger.info("- %s", cmd_msg)
    thread = threading.Thread(target=watchdog)
    thread.daemon = True
    thread.start()


class LocalShell(spur.LocalShell):

    __CHILDREN = collections.OrderedDict()

    def _child_add(self, obj, kill):
        LocalShell.__CHILDREN[obj] = (self, kill)

    def _child_remove(self, obj):
        del LocalShell.__CHILDREN[obj]

    def __init__(self, *args, **kwargs):
        spur.LocalShell.__init__(self, *args, **kwargs)

    def run(self, *args, **kwargs):
        cmd = args[0]
        cmd_msg = " ".join(cmd)
        logger.info("+ %s", cmd_msg)
        obj = spur.LocalShell.spawn(self, store_pid=True, *args, **kwargs)
        self._child_add(obj, None)
        res = obj.wait_for_result()
        self._child_remove(obj)
        logger.info("- %s", cmd_msg)
        return res

    def spawn(self, *args, **kwargs):
        exit_on_error = kwargs.pop("exit_on_error", True)
        cmd = args[0]
        cmd_msg = " ".join(cmd)
        logger.info("+ %s", cmd_msg)
        kill = kwargs.pop("kill", None)
        obj = spur.LocalShell.spawn(self, store_pid=True, *args, **kwargs)
        obj._is_killed = False
        self._child_add(obj, kill)
        _watchdog_thread(self, obj, cmd_msg, exit_on_error)
        return obj

    @staticmethod
    def _atexit_cb():
        global _EXITING
        _EXITING = True
        for child, (shell, kill) in LocalShell.__CHILDREN.items():
            child._is_killed = True
            if child.is_running():
                try:
                    if kill:
                        shell.run(kill)
                    else:
                        child.send_signal(signal.SIGKILL)
                    child.wait_for_result()
                except:
                    pass

class SshShell(spur.SshShell):

    __CHILDREN = collections.OrderedDict()

    def _child_add(self, obj, kill):
        SshShell.__CHILDREN[obj] = (self, kill)

    def _child_remove(self, obj):
        del SshShell.__CHILDREN[obj]

    def __init__(self, *args, **kwargs):
        spur.SshShell.__init__(self, *args, **kwargs)
        self.hostname = self._hostname
        self.username = self._username

    def run(self, *args, **kwargs):
        cmd = args[0]
        cmd_msg = "ssh -p %d %s@%s %s" % (self._port, self.username, self.hostname, " ".join(cmd))
        logger.info("+ %s", cmd_msg)
        obj = spur.SshShell.spawn(self, store_pid=True, *args, **kwargs)
        self._child_add(obj, None)
        res = obj.wait_for_result()
        self._child_remove(obj)
        logger.info("- %s", cmd_msg)
        return res

    def spawn(self, *args, **kwargs):
        exit_on_error = kwargs.pop("exit_on_error", True)
        cmd = args[0]
        cmd_msg = "ssh -p %d %s@%s %s" % (self._port, self.username, self.hostname, " ".join(cmd))
        logger.info("+ %s", cmd_msg)
        kill = kwargs.pop("kill", None)
        obj = spur.SshShell.spawn(self, store_pid=True, *args, **kwargs)
        obj._is_killed = False
        self._child_add(obj, kill)
        _watchdog_thread(self, obj, cmd_msg, exit_on_error)
        return obj

    @staticmethod
    def _atexit_cb():
        global _EXITING
        _EXITING = True
        for child, (shell, kill) in SshShell.__CHILDREN.items():
            child._is_killed = True
            if child.is_running():
                try:
                    if kill:
                        shell.run(kill)
                    else:
                        child.send_signal(signal.SIGKILL)
                    child.wait_for_result()
                except:
                    pass


# Kill all remaining processes when exiting
atexit.register(LocalShell._atexit_cb)
atexit.register(SshShell._atexit_cb)
def _signal_handler(signum, frame):
    sys.exit(1)
signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGQUIT, _signal_handler)
