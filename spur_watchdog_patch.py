#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import print_function
from __future__ import absolute_import


import atexit
import logging
import os
import signal
import spur
import sys
import threading
import traceback
import time


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# Monitor background processes for failures, so we can error out early, and
# kill all processes when exiting

def _watchdog_thread(obj, cmd_msg):
    stack_info = traceback.extract_stack()
    def watchdog():
        try:
            obj.wait_for_result()
        except Exception as e:
            if not obj._is_killed:
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
                os._exit(1)
        logger.info("-- %s", cmd_msg)
    thread = threading.Thread(target=watchdog)
    thread.daemon = True
    thread.start()


class LocalShell(spur.LocalShell):

    __CHILDREN = []

    def __init__(self, *args, **kwargs):
        spur.LocalShell.__init__(self, *args, **kwargs)

    def run(self, *args, **kwargs):
        cmd = args[0]
        cmd_msg = " ".join(cmd)
        logger.info("+ %s", cmd_msg)
        obj = spur.LocalShell.spawn(self, store_pid=True, *args, **kwargs)
        self.__CHILDREN.append((obj, self, None))
        res = obj.wait_for_result()
        logger.info("- %s", cmd_msg)
        return res

    def spawn(self, *args, **kwargs):
        cmd = args[0]
        cmd_msg = " ".join(cmd)
        logger.info("+- %s", cmd_msg)
        kill = kwargs.pop("kill", None)
        obj = spur.LocalShell.spawn(self, store_pid=True, *args, **kwargs)
        obj._is_killed = False
        self.__CHILDREN.append((obj, self, kill))
        logger.info("-+ %s", cmd_msg)
        _watchdog_thread(obj, cmd_msg)
        return obj

    @staticmethod
    def _atexit_cb():
        for child, shell, kill in LocalShell.__CHILDREN:
            child._is_killed = True
            if child.is_running():
                if kill:
                    shell.run(kill)
                else:
                    try:
                        child.send_signal(signal.SIGKILL)
                    except spur.results.RunProcessError:
                        pass

class SshShell(spur.SshShell):

    __CHILDREN = []

    def __init__(self, *args, **kwargs):
        spur.SshShell.__init__(self, *args, **kwargs)
        self.hostname = self._hostname
        self.username = self._username

    def run(self, *args, **kwargs):
        cmd = args[0]
        cmd_msg = "ssh %s@%s %s" % (self.username, self.hostname, " ".join(cmd))
        logger.info("+ %s", cmd_msg)
        obj = spur.SshShell.spawn(self, store_pid=True, *args, **kwargs)
        self.__CHILDREN.append((obj, self, None))
        res = obj.wait_for_result()
        logger.info("- %s", cmd_msg)
        return res

    def spawn(self, *args, **kwargs):
        cmd = args[0]
        cmd_msg = "ssh %s@%s %s" % (self.username, self.hostname, " ".join(cmd))
        logger.info("+- %s", cmd_msg)
        kill = kwargs.pop("kill", None)
        obj = spur.SshShell.spawn(self, store_pid=True, *args, **kwargs)
        obj._is_killed = False
        self.__CHILDREN.append((obj, self, kill))
        logger.info("-+ %s", cmd_msg)
        _watchdog_thread(obj, cmd_msg)
        return obj

    @staticmethod
    def _atexit_cb():
        for child, shell, kill in SshShell.__CHILDREN:
            child._is_killed = True
            if child.is_running():
                if kill:
                    shell.run(kill)
                else:
                    try:
                        child.send_signal(signal.SIGKILL)
                    except spur.results.RunProcessError:
                        pass


atexit.register(LocalShell._atexit_cb)
atexit.register(SshShell._atexit_cb)
