#!/usr/bin/env python3
import time
import yaml
import logging
import argparse
import pprint
import requests
import re
import sys
import time
from logging.config import dictConfig

from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, InfoMetricFamily, StateSetMetricFamily, REGISTRY
from prometheus_client import start_http_server
    
logging_config = dict(
    version = 1,
    formatters = {
        'f': {'format':
              '%(asctime)s %(levelname)-8s [%(funcName)s:%(lineno)d] %(message)s'}
    },
    handlers = {
        'c': {'class': 'logging.StreamHandler',
              'formatter': 'f',
              'level': logging.DEBUG,
              'stream': "ext://sys.stdout" }
    },
    root = {
        'handlers': ['c'],
        'level': logging.INFO,
    },
)

dictConfig(logging_config)
logger = logging.getLogger(__name__)

""" TPLinkSwitch class stores the variables relevant for each switch """
class TPLinkSwitch(object):

    def __init__(self, configuration):
        """ Take configuration from the YAML file and create a new switch """
        self.ip = configuration['ip']
        self.username = configuration['username']
        self.password = configuration['password']
        if 'cache_login' in configuration:
            self.cache_login = configuration['cache_login']
        else:
            #by default don't cache logins, since they expire pretty quickly anyway
            self.cache_login = False
        if 'http_port' in configuration:
            self.http_port = int(configuration['http_port'])
        else:
            self.http_port = 80

        """ This is a mapping of GUI status values to their meaning """
        self.mapping = {
            '0': "down", 
            '1': "auto", 
            '2': "10/half", 
            '3': "10/full", 
            '4': "100/half", 
            '5': "100/full",
            '6': "1000/full"
        }

        self.base_url = "http://"+str(self.ip)+":"+str(self.http_port)

        self.session = requests.Session()
        self.ports = {}
        if 'port_descriptions' in configuration:
            logger.debug("configuration['port_descriptions']: "+pprint.pformat(configuration['port_descriptions']))
            for port in configuration['port_descriptions']:
                self.ports[port] = configuration['port_descriptions'][port]
    
    def getIP(self):
        return self.ip

    def getCacheLogin(self):
        return self.cache_login

    def __str__(self):
        return "{}@{} with {} ports".format(self.username, self.ip, len(self.ports))

    def loggedIn(self):
        return self.loggedin

    def login(self):
        data = {"logon": "Login", "username": self.username, "password": self.password}
        headers = { 'Referer': self.base_url+"/Logout.htm"}
        try:
            r = self.session.post(self.base_url+'/logon.cgi', data=data, headers=headers, timeout=5)
            logger.debug("Logged in:"+r.text)
            self.loggedin = True
            return True
        except requests.exceptions.Timeout as errt:
            logger.error("Timeout on login for {}@{}: {}".format(self.username, self.ip, str(errt)))
            self.loggedin = False
            return False
        except requests.exceptions.RequestException as err:
            logger.error("Error on login for {}@{}: {}".format(self.username, self.ip, str(err)))
            self.loggedin = False
            return False

    def getPortStateMapping(self, state):
        if state in self.mapping:
            return self.mapping[state]
        else:
            return None

    def getStats(self):
        """ The measurements are returned as a dictionary in this form:
            stats[port][description]
            stats[port][state]
            stats[port][link_status]
            stats[port][txGoodPkt]
            stats[port][txBadPkt]
            stats[port][rxGoodPkt]
            stats[port][rxBadPkt]
        """
        stats = {}
        headers = {'Referer': self.base_url+'/',
           'Accept': "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
           'Upgrade-Insecure-Requests': "1" }
        try:
            r = self.session.get(self.base_url+"/PortStatisticsRpm.htm", headers=headers, timeout=5)
            logger.debug("Received stats:"+r.text)

            # we're looking for something like this in the output:
            #state:[1,1,1,0,1,1,1,1,0,0],
            #link_status:[6,5,6,0,0,0,6,6,0,0],
            #pkts:[156660032,0,73463961,0,36934785,0,18955572,0,224590711,0,67687216,0,54497978,0,29301491,0,0,0,0,0,0,0,0,0,32241294,0,25417373,0,209448595,0,462006278,0,0,0]

            # state: 1 - Enabled, 0 - Disabled (administratively)
            # link_status: 0 - down, 1 - auto, 2 - 10Mbps half, 3 - 10Mbps full, 4 - 100Mbps half, 5 - 100Mbps full, 6 - 1Gbps full
            # pkts: every group of 4 values represent txGoodPkt, txBadPkt, rxGoodPkt, rxBadPkt

            stateMatch = re.search(r'state:\[([0-9,]+)\]', r.text)
            numberOfPorts = 0
            if stateMatch:
                state = stateMatch.group(1).split(",")
                ### Note - that the last two entries in the json data do not correspond to physical ports,
                ### so we'll skip them here...
                state = state[:-2]
                # populate the stats dictionary
                for p in range(1,len(state)+1):
                    p = str(p)
                    if p not in stats:
                        stats[p] = {}
                        if p in self.ports:
                            stats[p]['description'] = self.ports[p]
                        else:
                            stats[p]['description'] = ""
                        stats[p]['state'] = state[int(p)-1]
                        numberOfPorts+=1
            
            linkStatusMatch = re.search(r'link_status:\[([0-9,]+)\]', r.text)
            if linkStatusMatch:
                state = linkStatusMatch.group(1).split(",")
                ### Note - that the last two entries in the json data do not correspond to physical ports,
                ### so we'll skip them here...
                state = state[:-2]
                # populate the stats dictionary
                for p in range(1,len(state)+1):
                    p = str(p)
                    stats[p]['link_status'] = self.getPortStateMapping(state[int(p)-1])

            pktsMatch = re.search(r'pkts:\[([0-9,]+)\]', r.text)
            if pktsMatch:
                pktData = pktsMatch.group(1).split(",")
                ### Note - that the last two entries in the json data do not correspond to physical ports,
                ### so we'll skip them here...
                pktData = pktData[:-2]

                # data in the array has 4 measurements for each port, in order
                for p in range(1, numberOfPorts + 1):
                    stats[str(p)]['txGoodPkt'] = pktData[ (p - 1)* 4]
                    stats[str(p)]['txBadPkt']  = pktData[ (p - 1)* 4 + 1]
                    stats[str(p)]['rxGoodPkt'] = pktData[ (p - 1)* 4 + 2]
                    stats[str(p)]['rxBadPkt']  = pktData[ (p - 1)* 4 + 3]

        except requests.exceptions.Timeout as errt:
            logger.error("Timeout on stats read for {}@{}: {}".format(self.username, self.ip, str(errt)))
            return stats
        except requests.exceptions.RequestException as err:
            logger.error("Error on stats read for {}@{}: {}".format(self.username, self.ip, str(err)))
            return stats
        return stats

class CustomCollector(object):
    def __init__(self, switches):
        self.switches = switches

    def collect(self):
        """ This gets called when the /metrics endpoint gets scraped by each client """

        startTime = time.perf_counter()
        logger.info("Collecting data...")        
        
        # return global metrics here
        # set up the Prometheus metrics that we'll be exporting

        # packets exports the packet counter for each port
        # the switch doesn't provide bits per port, packet sizes or other meaningful data
        # so the best you can do is measure packets per second
        packets = CounterMetricFamily('tplink_sg_switch_port_packet_counters', 'Packet counters (rx/tx, good/bad) for each switch port', labels=['host','port','type'], unit='packets')

        linkSpeedList = []
        # kind of hard-coded, I know, but it shouldn't change for current switches
        for i in range(0, 7):
            linkSpeedList.append(self.switches[0].getPortStateMapping(str(i)))

        linkSpeed = StateSetMetricFamily('tplink_sg_switch_port_linkSpeed', 'Link speed/duplex for each switch port', labels=['host','port'])
        
        linkState = StateSetMetricFamily('tplink_sg_switch_port_linkState', 'Administrative state (enabled/disabled) for each switch port', labels=['host','port'])

        portDescription = InfoMetricFamily('tplink_sg_switch_port_description', 'Port descriptions for each port', labels=['host', 'port'])

        # return data for each switch here
        for current_switch in self.switches:
            logger.info("Looking at {}".format(current_switch.getIP()))
            if(not current_switch.getCacheLogin()):
                # if logins are not cached, we need to login to get data
                current_switch.login()
            if not current_switch.loggedIn():
                logger.warning("Trying to re-login to {}".format(current_switch.getIP()))
                # try to log in in case something happened
                current_switch.login()
            if current_switch.loggedIn():
                stats = current_switch.getStats()
                if len(stats) == 0:
                    # we didn't get any results, so there must be some login issue
                    logger.warning("Couldn't get any results from {}.".format(current_switch.getIP()))
                logger.debug("Received stats:"+pprint.pformat(stats))
                for port in stats:
                    for pktType in ('rxGoodPkt', 'rxBadPkt', 'txGoodPkt', 'txBadPkt'):
                        #prepare port statistics
                        label = [current_switch.getIP(), port, pktType]
                        packets.add_metric( label, int(stats[port][pktType]))

                    #prepare link speed. We need to pass a dictionary with all states and booleans
                    allLinkSpeed = {}
                    for speed in linkSpeedList:
                        if speed == stats[port]['link_status']:
                            allLinkSpeed[speed] = True
                        else:
                            allLinkSpeed[speed] = False


                    linkSpeed.add_metric(labels=(current_switch.getIP(), port), value=allLinkSpeed)                

                    #prepare link status. We need to pass a dictionary with all states and booleans
                    allLinkStates = {}
                    if stats[port]['state'] == '0':
                        allLinkStates['disabled'] = True
                        allLinkStates['enabled'] = False
                    else:
                        allLinkStates['disabled'] = False
                        allLinkStates['enabled'] = True

                    linkState.add_metric(labels=(current_switch.getIP(), port), value=allLinkStates)                

                    #prepare port descriptions
                    portDescription.add_metric(labels=(current_switch.getIP(), port), value={'description': stats[port]['description']})


                yield packets
                yield linkSpeed
                yield linkState
                yield portDescription


        # finished with all the switches. Calculate how long it took
        endTime = time.perf_counter()
        duration = GaugeMetricFamily('tplink_sg_collection_time', 'Collection time in seconds for all the switches')
        duration.add_metric([], endTime-startTime)
        yield duration
        logger.info("Finished collecting...")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Export packet data from TL-SG-10x switches to prometheus")
    parser.add_argument('--config', type=str, nargs=1, required=True, dest='configuration_file',
                        help='path to the yaml configuration file')

    args = parser.parse_args()
    conf = {}

    def parseConfig():
        global conf
        global args
        with open(args.configuration_file[0], 'r') as stream:
            try:
                conf = yaml.load(stream, Loader=yaml.SafeLoader)
            except yaml.YAMLError as exc:
                logger.error(exc)
                logger.error("Unable to parse configuration file "+args.configuration_file)
                sys.exit(1)

    parseConfig()
    #logger.debug("Loaded config: "+pprint.pformat(conf))

    switches = []

    logger.info("Loading switch configuration...")
    if 'switch' in conf:
        # create the switches to be monitored
        for current_switch in conf['switch']:
            newSwitch = TPLinkSwitch(current_switch)
            # try to log into the switch at startup
            newSwitch.login()
            switches.append(newSwitch)  

    else:
        logger.fatal("Missing switch definition in yaml conf file.")
        sys.exit(2)

    logger.info("Loaded {} switches".format(len(switches)))
    #initialize the prometheus collector class
    collector = CustomCollector(switches)
    REGISTRY.register(collector)

    http_port = conf['http_port']
    logger.info("Starting HTTP server on port {}".format(int(http_port)))
    start_http_server(int(http_port))
    # this infinite loop is just to keep the server listening...
    # processing is being done when clients access the /metrics endpoint
    # and CustomCollector.collect() is called
    while True:
        time.sleep(1000)  

