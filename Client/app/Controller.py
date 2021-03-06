import Scheduler, MqttAdapter, AmqpAdapter, Measurements, CoapAdapter
import logging
import subprocess
import uuid
import time


class Controller:

    def __init__(self):
        self.adapter = None
        self.measurements = None
        self.subscriptions = []
        self.scheduler = None
        self.broker_address = ""
        self.number_of_clients = []
        self.start_time = 0
        self.responses = {}
        self.payloads = {}
        self.name = ""
        self.network_parameters = {"iptables": False, "delay": False, "bandwidth": False}
        self.current_packet_loss = 0
        self.port = None
        self.warning_logger = None
        self.tp = ""

    async def create_components(self, name, start_time, runtime, broker_address, protocol, settings):
        self.subscriptions = []
        self.number_of_clients = []
        self.responses = {}

        file = open("{0}warning.log".format(name), 'w')
        file.truncate(0)
        file.close()
        self.warning_logger = logging.getLogger("WarningLogger")
        self.warning_logger.handlers.clear()
        self.warning_logger.addHandler(logging.FileHandler("{0}warning.log".format(name)))
        self.warning_logger.setLevel(logging.INFO)

        self.scheduler = Scheduler.Scheduler(self, start_time, runtime)
        self.scheduler.start_scheduler()
        self.broker_address = broker_address
        self.start_time = start_time
        self.scheduler.schedule_stop()
        self.scheduler.schedule_resource_measuring()

        if not self.check_ntp_synchronization():
            self.warning_logger.info("Time is not synchronized with ntp")
        if not self.check_ptp_synchronization():
            self.warning_logger.info("Time is not synchronized with ptp")

        self.set_adapter(protocol, name)
        if isinstance(self.adapter, MqttAdapter.MqttAdapter):
            await self.adapter.connect(broker_address, settings)
        else:
            print(broker_address)
            print(isinstance(self.adapter, CoapAdapter.CoapAdapter))
            await self.adapter.connect(broker_address)

    async def configure(self, protocol, broker_address, start_time, runtime, quality_class, name, settings):

        if protocol == "MQTT":
            if not settings.tls:
                self.port = 1883
            else:
                self.port = 8883
        elif protocol == "AMQP":
            self.port = 5672
        elif protocol == "CoAP":
            self.port = 5683
        print("Protocol: " + protocol)

        if quality_class is not None:
            packet_loss = quality_class.packet_loss
            bandwidth = quality_class.bandwidth
            delay = quality_class.delay
            time_series = quality_class.time_series

            if time_series is not None:
                for item in time_series:
                    if item[1] is not None:
                        self.network_parameters["iptables"] = True
                    if item[2] is not None:
                        self.network_parameters["bandwidth"] = True
                    if item[3] is not None:
                        self.network_parameters["delay"] = True
                self.scheduler.schedule_time_series(time_series)

            if packet_loss is not None:
                self.set_packet_loss(packet_loss)
                self.network_parameters["iptables"] = True
            elif self.network_parameters["iptables"]:
                self.set_packet_loss(0.0)
            if bandwidth is None and self.network_parameters["bandwidth"]:
                bandwidth = "1000tbps"
            if delay is None and self.network_parameters["delay"]:
                delay = 0

            if bandwidth is not None or delay is not None:
                if bandwidth is None:
                    bandwidth = "1000tbps"
                if delay is None:
                    delay = 0
                self.set_bandwidth_and_network_delay(bandwidth, delay)
                self.network_parameters["bandwidth"] = True
                self.network_parameters["delay"] = True

        if self.network_parameters["bandwidth"] or self.network_parameters["delay"]:
            self.measurements = Measurements.Measurements(name, ["eth0", "ifb0"])
        else:
            self.measurements = Measurements.Measurements(name, ["eth0", "eth0"])
        self.name = name

    @staticmethod
    def check_ptp_synchronization() -> bool:
        p = subprocess.Popen('pgrep ptp4l', stdout=subprocess.PIPE, shell=True)
        (output, err) = p.communicate()
        return Controller.is_ptp_synchronized_from_process(output.decode('utf-8'))

    @staticmethod
    def is_ptp_synchronized_from_process(output: str) -> bool:
        """ Parses the output from process look up to to determine if PTP is running.
         If PTP is running, TRUE is returned, otherwise FALSE """
        return not output == ""

    @staticmethod
    def check_ntp_synchronization():
        p = subprocess.Popen('pgrep ntpd', stdout=subprocess.PIPE, shell=True)
        (output, err) = p.communicate()
        if not Controller.is_ntp_synchronized_from_process(output.decode("utf-8")):
            return False
        else:
            p = subprocess.Popen('ntpstat; echo $?', stdout=subprocess.PIPE, shell=True)
            (output, err) = p.communicate()
            return Controller.is_ntp_synchronized_from_ntpstat(output.decode('utf-8'))

    @staticmethod
    def is_ntp_synchronized_from_process(output: str) -> bool:
        """ Parses the output from process look up to to determine if NTP is running.
         If NTP is running, TRUE is returned, otherwise FALSE """
        return not output == ""

    @staticmethod
    def is_ntp_synchronized_from_ntpstat(output: str) -> bool:
        """ Parses the output from ntpstat to to determine the NTP status.
         If NTP is working well, TRUE is returned, otherwise FALSE """
        return int(output[-2]) == 0

    def set_packet_loss(self, loss):
        cmd = "iptables -A INPUT -p {0} --sport {1} -m statistic --mode random --probability {2} -j DROP".format(
            self.tp, self.port, loss)
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, shell=True)
        (output, err) = p.communicate()

        cmd = "iptables -A OUTPUT -p {0} --dport {1} -m statistic --mode random --probability {2} -j DROP".format(
            self.tp, self.port, loss)
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, shell=True)
        (output, err) = p.communicate()
        self.current_packet_loss = loss
        # output an Benchmark suite zurückgeben

    def set_bandwidth_and_network_delay(self, bandwidth, delay):
        p = subprocess.Popen("modprobe ifb numifbs=1", stdout=subprocess.PIPE, shell=True)
        (output, err) = p.communicate()

        p = subprocess.Popen("kmod list | grep -w ifb", stdout=subprocess.PIPE, shell=True)
        (output, err) = p.communicate()

        if output is None:
            self.warning_logger.info("Could not find ifb module!")
        else:
            p = subprocess.Popen("ip link add ifb0 type ifb", stdout=subprocess.PIPE, shell=True)
            (output, err) = p.communicate()

            p = subprocess.Popen("ip link set dev ifb0 up", stdout=subprocess.PIPE, shell=True)
            (output, err) = p.communicate()

            p = subprocess.Popen("tc qdisc add dev eth0 handle ffff: ingress", stdout=subprocess.PIPE, shell=True)
            (output, err) = p.communicate()

            p = subprocess.Popen(
                "tc filter add dev eth0 parent ffff: protocol ip u32 match u32 0 0 action mirred egress redirect dev ifb0",
                stdout=subprocess.PIPE, shell=True)
            (output, err) = p.communicate()

            p = subprocess.Popen("tc qdisc add dev ifb0 handle 1: root htb", stdout=subprocess.PIPE, shell=True)
            (output, err) = p.communicate()

            p = subprocess.Popen("tc class add dev ifb0 parent 1: classid 1:15 htb rate {0}".format(bandwidth),
                                 stdout=subprocess.PIPE, shell=True)
            (output, err) = p.communicate()

            p = subprocess.Popen("tc qdisc add dev ifb0 parent 1:15 handle 20: netem delay {0}ms".format(delay),
                                 stdout=subprocess.PIPE, shell=True)
            (output, err) = p.communicate()

            p = subprocess.Popen(
                "tc filter add dev ifb0 protocol ip parent 1: prio 1 u32 match ip sport {0} 0xffff flowid 1:15".format(
                    self.port), stdout=subprocess.PIPE, shell=True)
            (output, err) = p.communicate()

        p = subprocess.Popen("tc qdisc add dev eth0 handle 1: root htb", stdout=subprocess.PIPE, shell=True)
        (output, err) = p.communicate()

        p = subprocess.Popen("tc class add dev eth0 parent 1: classid 1:15 htb rate {0}".format(bandwidth),
                             stdout=subprocess.PIPE,
                             shell=True)
        (output, err) = p.communicate()

        p = subprocess.Popen("tc qdisc add dev eth0 parent 1:15 handle 20: netem delay {0}ms".format(delay),
                             stdout=subprocess.PIPE,
                             shell=True)
        (output, err) = p.communicate()

        p = subprocess.Popen(
            "tc filter add dev eth0 protocol ip parent 1: prio 1 u32 match ip dport {0} 0xffff flowid 1:15".format(
                self.port), stdout=subprocess.PIPE, shell=True)
        (output, err) = p.communicate()

    def change_network_configuration(self, packet_loss, bandwidth, delay):
        if packet_loss is not None:
            cmd = "iptables -D INPUT -p {0} --sport {1} -m statistic --mode random --probability {2} -j DROP".format(
                self.tp, self.port, self.current_packet_loss)
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, shell=True)
            (output, err) = p.communicate()
            cmd = "iptables -A INPUT -p {0} --sport {1} -m statistic --mode random --probability {2} -j DROP".format(
                self.tp, self.port, packet_loss)
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, shell=True)
            (output, err) = p.communicate()

            cmd = "iptables -D OUTPUT -p {0} --dport {1} -m statistic --mode random --probability {2} -j DROP".format(
                self.tp, self.port, self.current_packet_loss)
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, shell=True)
            (output, err) = p.communicate()
            cmd = "iptables -A OUTPUT -p {0} --dport {1} -m statistic --mode random --probability {2} -j DROP".format(
                self.tp, self.port, packet_loss)
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, shell=True)
            (output, err) = p.communicate()

            self.current_packet_loss = packet_loss

        if bandwidth is not None:
            p = subprocess.Popen("tc class change dev eth0 parent 1: classid 1:15 htb rate {0}".format(bandwidth),
                                 stdout=subprocess.PIPE,
                                 shell=True)
            (output, err) = p.communicate()

        if bandwidth is not None:
            p = subprocess.Popen("tc class change dev ifb0 parent 1: classid 1:15 htb rate {0}".format(bandwidth),
                                 stdout=subprocess.PIPE,
                                 shell=True)
            (output, err) = p.communicate()

        if delay is not None:
            p = subprocess.Popen("tc qdisc change dev eth0 parent 1:15 handle 20: netem delay {0}ms".format(delay),
                                 stdout=subprocess.PIPE,
                                 shell=True)
            (output, err) = p.communicate()

        if delay is not None:
            p = subprocess.Popen("tc qdisc change dev ifb0 parent 1:15 handle 20: netem delay {0}ms".format(delay),
                                 stdout=subprocess.PIPE,
                                 shell=True)
            (output, err) = p.communicate()

    def remove_packet_loss(self, loss):
        cmd = "iptables -D INPUT -p {0} --sport {1} -m statistic --mode random --probability {2} -j DROP".format(
            self.tp, self.port, loss)
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, shell=True)
        (output, err) = p.communicate()

        cmd = "iptables -D OUTPUT -p {0} --dport {1} -m statistic --mode random --probability {2} -j DROP".format(
            self.tp, self.port, loss)
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, shell=True)
        (output, err) = p.communicate()
        # output an Benchmark suite zurückgeben

    def remove_qdisc_rules(self):
        p = subprocess.Popen("tc qdisc del dev eth0 root", stdout=subprocess.PIPE,
                             shell=True)
        (output, err) = p.communicate()

        p = subprocess.Popen("tc qdisc del dev eth0 ingress", stdout=subprocess.PIPE,
                             shell=True)
        (output, err) = p.communicate()

        p = subprocess.Popen("tc qdisc del dev ifb0 root", stdout=subprocess.PIPE,
                             shell=True)
        (output, err) = p.communicate()

    async def start_client(self):
        await self.adapter.start_client()

    async def stop_client(self):
        identifier = uuid.uuid1()
        timestamp = time.time_ns()
        topic = "quit run"
        self.measurements.register_info("q", identifier, timestamp, topic)
        if self.network_parameters["iptables"]:
            self.remove_packet_loss(self.current_packet_loss)
        if self.network_parameters["bandwidth"] or self.network_parameters["delay"]:
            self.remove_qdisc_rules()
        await self.adapter.stop_client()

    def set_adapter(self, protocol, name):
        if protocol == "MQTT":
            self.adapter = MqttAdapter.MqttAdapter(name, self)
            self.tp = "tcp"
        elif protocol == "AMQP":
            self.adapter = AmqpAdapter.AmqpAdapter(name, self)
            self.tp = "tcp"
        elif protocol == "CoAP":
            print("Setting adapter")
            self.adapter = CoapAdapter.CoapAdapter(self)
            self.tp = "udp"

    async def manage_subscription(self, subscription, settings):
        self.subscriptions.append(subscription)
        await self.adapter.subscribe(subscription, settings)
        logging.info("Subscribed to topic: %s", subscription)

    def manage_publishing(self, topic, payload_size, trigger_type, subscription, timed_variable, value, settings):
        if subscription is not None:
            if subscription not in self.subscriptions:
                raise Exception("There is no subscription: " + subscription)
        if trigger_type == "interval":
            self.scheduler.schedule_start_publishing(topic, timed_variable, value, settings)
        elif trigger_type == "reaction":
            if subscription in self.responses:
                self.responses[subscription].append([topic, timed_variable, value, settings])
            else:
                self.responses[subscription] = [[topic, timed_variable, value, settings]]
        if payload_size <= 36:
            self.payloads[topic] = ""
        else:
            self.payloads[topic] = "A" * (payload_size - 36)

    def react(self, topic, message):
        timestamp = time.time_ns()
        identifier = message[:36]
        self.measurements.register_received(identifier, timestamp)
        response = self.responses.get(topic)
        if response is not None:
            for rsp in response:
                self.scheduler.schedule_reaction(rsp[0], rsp[1], rsp[2], rsp[3])

    async def publish(self, topic, settings):
        identifier = str(uuid.uuid1())
        timestamp = time.time_ns()
        await self.adapter.publish(topic, identifier, self.payloads[topic], settings)
        self.measurements.register_sent(identifier, timestamp, topic)

    def start_resource_measuring(self, runtime):
        self.measurements.measure_resources(runtime)
