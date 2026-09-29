import agent

try:
    import wifi
except ImportError:
    raise SystemExit("copy firmware/wifi.example.py to firmware/wifi.py and edit it")

ip = agent.connect(wifi.SSID, wifi.PASSWORD)
print("connected: " + str(ip))
agent.serve(port=80)
