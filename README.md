# REPL for pybricks

Terminal program to start a REPL on a pybricks device.

This terminal program uses the Nordic Semiconductor (nRF) UART service in the bluetooth low energy(BLE) protocol.
It reads in raw mode from stdin and sends each char read to the remote device. In this way any special control/escape
characters are handled in the REPL program on the device. (e.g. needed to get TAB completion in the REPL working)
Any data received from the device is directly printed to stdout.
