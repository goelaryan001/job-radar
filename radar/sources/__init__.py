from . import ats, bigtech, github_lists, handshake

COLLECTORS = {**ats.COLLECTORS, **bigtech.COLLECTORS, **github_lists.COLLECTORS, **handshake.COLLECTORS}
