"""Resolve every hostname to 127.0.0.1, so one mock server plays thousands of hosts."""
from scrapy.resolver import CachingThreadedResolver
from twisted.internet import defer


class LoopbackResolver(CachingThreadedResolver):
    def getHostByName(self, name, timeout=None):
        return defer.succeed("127.0.0.1")
