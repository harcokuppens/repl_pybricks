"""
REPL for Pybricks
-----------------

Terminal program to start a REPL on a pybricks device.

This "terminal" program uses the Nordic Semiconductor (nRF) UART service in the bluetooth low energy(BLE) protocol.
It reads in raw mode from stdin and sends each char read to the remote device. In this way any special control/escape
characters are handled in the REPL program on the device. (e.g. needed to get TAB completion in the REPL working)
Any data received from the device is directly printed to stdout.

"""

import asyncio
import sys
import struct
import time
import blessed
import platform
    

from bleak import BleakClient, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData


# nordic uart service (NUS)
UART_SERVICE_UUID = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
UART_RX_CHAR_UUID = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
UART_TX_CHAR_UUID = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"


# pybricks service
def _pybricks_uuid(short: int) -> str:
    return f"c5f5{short:04x}-8280-46da-89f4-6d8051e4aeef"


PYBRICKS_SERVICE_UUID = _pybricks_uuid(0x0001)
"""The Pybricks GATT service UUID."""
PYBRICKS_COMMAND_EVENT_UUID = _pybricks_uuid(0x0002)
"""The Pybricks command/event GATT characteristic UUID.

Commands are written to this characteristic and events are received via notifications.

See :class:`Command` and :class:`Event`.

.. availability:: Since Pybricks protocol v1.0.0.
"""
PYBRICKS_HUB_CAPABILITIES_UUID = _pybricks_uuid(0x0003)
"""The Pybricks hub capabilities GATT characteristic UUID.

.. availability:: Since Pybricks protocol v1.2.0.
"""

import threading

async def uart_terminal(name = "Pybricks Hub"):
    """This a "terminal" program that uses the Nordic Semiconductor
    (nRF) UART service. It reads in raw mode from stdin and sends each char read to the
    remote device. Any data received from the device is directly printed to stdout.
    """

    timeout = 10
    service = PYBRICKS_SERVICE_UUID
    
    
    
    # code to do a gracefull shutdown of program on brick when this program 
    # gets closed (e.g. when console windows is closed)
    STOP_PROGRAM = False
    if platform.system() == "Windows": 
          # Asyncio for windows does not implement loop.add_signal_handler YET,
          # so we have to use signal module instead.
          # 
          # There is a signal module for windows, but  
          # - SIGHUP is not implemented for windows
          # - SIGTERM is recognized but not working (handler never called)
          # - SIGINT is the only one which should work on windows, which
          #          by default is handled by python by raising a KeyboardInterrupt
          #          exception
          # - SIGBREAK is a special windows only signal which is received
          #            either when pressing CTRL-BREAK or when closing
          #            console/terminal window. However only the key event 
          #            works for python programs. To also support the close
          #            window event we are forced to use the win32api.
          #

          # So on windows we therefore use by exception the win32api
          #  -> more details see documentation at end of this document
          
          # clean exit doen when pressing CTRL-BREAK or closing terminal/console window
          # note: end task from taskmanager does always a hard kill which cannot be
          #       catched in the program to do a clean exit.
          def on_exit(signal_type):  
               # on_exit seems to run in a dummy thread different from main thread
               nonlocal STOP_PROGRAM
               STOP_PROGRAM = True
               print("shutdown",flush=True)
               # let on_exit in this dummy thread sleep, so that event loop 
               # in other thread gets the time to close its asyncs tasks nicely.
               time.sleep(1)
 
          import win32api
          win32api.SetConsoleCtrlHandler(on_exit, True)
    else:
        import signal
        # src: https://www.roguelynn.com/words/asyncio-graceful-shutdowns/
        signals = (signal.SIGHUP, signal.SIGTERM, signal.SIGINT)
        async def shutdown():
            nonlocal STOP_PROGRAM
            STOP_PROGRAM = True
            print("shutdown",flush=True)
            # current thread is MainThread which we let sleep for a second
            # to be sure the eventloop in its own thread can finish
            time.sleep(1)

        loop = asyncio.get_running_loop()
        for s in signals:
            loop.add_signal_handler(
                s, lambda s=s: asyncio.create_task(shutdown()))
        
        ## another way to shutdown: cancel all tasks and wait for it        
        # async def shutdown(loop):
        #     print("shutdown",flush=True)
        #     tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        #     [task.cancel() for task in tasks]
        #     await asyncio.gather(*tasks)
        #     loop.stop()
        #
        # loop = asyncio.get_running_loop()
        # for s in signals:
        #     loop.add_signal_handler(
        #         s, lambda s=s: asyncio.create_task(shutdown(loop)))

    def match_uuid_and_name(device: BLEDevice, adv: AdvertisementData):
        if service.lower() not in adv.service_uuids:
            return False

        if (
            name is not None
            and adv.local_name != name
            and device.address.upper() != name.upper()
        ):
            return False

        return True


    def handle_disconnect(_: BleakClient):
        print("\rDevice was disconnected, goodbye.")

    def handle_rx( sender: BleakGATTCharacteristic, data: bytearray):
        # received data from spike prime
        #data = data.replace(b"\r\n", b"\n")
        print(data.decode(),end='',flush=True)

    def pybricks_service_handler( _: int, data: bytes) -> None:
        nonlocal STOP_PROGRAM
        STATUS_REPORT = 0
        if data[0] == STATUS_REPORT:
            # decode the payload
            (flags,) = struct.unpack_from("<I", data, 1)
            # 7th bit of flags describes whether programming is running
            program_is_running_bit = (flags >> 6) & 1
            if program_is_running_bit == 0:
               # program is not running so stop program
               print("\nProgram on device stopped running.")
               STOP_PROGRAM = True
               

    async def getchar(term,loop):
        """ Read a char none-blocking: the event loop gets its turn during the read.

            Important that this is none-blocking because otherwise the
            program will only really terminate after user presses another key
            to make the read unblock.

            It works by wait internally with a small blocking timeout of 0.2s for a key press
            before reading the char none-blocking. Between these two async operations the event loop
            get its turn. Then when the program is terminated, then after the blocking timeout the
            event loop get its turn, and can close itself cleanly.
        """
        nonlocal STOP_PROGRAM
        while not STOP_PROGRAM:
             # without timeout of 0.5 second wait if keyboard pressed
             if await loop.run_in_executor(None, term.kbhit,0.2):
                 # only when a key is pressed we can read a char none-blocking
                 s = await loop.run_in_executor(None, term.getch)
                 return s
        return " "         

    print("searching for device: '{}'".format(name))

    device = await BleakScanner.find_device_by_filter(
       match_uuid_and_name, timeout, service_uuids=[service]
    )

    if device is None:
        print("no matching device found.")
        sys.exit(1)

    async with BleakClient(device, disconnected_callback=handle_disconnect) as client:
        
        await client.start_notify(UART_TX_CHAR_UUID, handle_rx)

        print("Connected, start typing and press ENTER...")

        loop = asyncio.get_running_loop()
        nus = client.services.get_service(UART_SERVICE_UUID)
        rx_char = nus.get_characteristic(UART_RX_CHAR_UUID)

        # start repl program on brick, waits on response 
        START_REPL = 2
        await client.write_gatt_char(
            PYBRICKS_COMMAND_EVENT_UUID,
            struct.pack("<B", START_REPL),
            response=True,
        )

        await client.start_notify(PYBRICKS_COMMAND_EVENT_UUID, pybricks_service_handler)

        # Implementing a real terminal program which puts stdin in raw mode so that things
        # like CTRL+C get passed to the remote device.
        # Set terminal in raw mode:
        term=blessed.Terminal()
        with term.raw():
            # in a loop read a line of input and send it to the spike prime
            try:
                while not STOP_PROGRAM:
                    # read a char none-blocking: the event loop gets its turn during the read
                    s = await getchar(term,loop)
                    data = bytearray()
                    data.extend(map(ord, s))
                    await client.write_gatt_char(rx_char, data)
            finally:
                # stop repl program on brick (does even stop the program on the brick when terminal is closed)
                # first send ctrl-c to stop any running program in the repl
                await client.write_gatt_char(rx_char, b'\x03')
                # then stop the repl program itself
                STOP_USER_PROGRAM = 0
                await client.write_gatt_char(
                    PYBRICKS_COMMAND_EVENT_UUID,
                    struct.pack("<B", STOP_USER_PROGRAM),
                    response=True,
                )
                await client.stop_notify(UART_TX_CHAR_UUID)
                await client.stop_notify(PYBRICKS_COMMAND_EVENT_UUID)

if __name__ == "__main__":
    try:
        asyncio.run(uart_terminal())
    except KeyboardInterrupt:
        # When interrupted before repl is started; often during scanning.
        # After the repl is started then all CTRL-C chars
        # are forwarded to remote repl on the device, and we cannot
        # kill the program anymore with CTRL-C. Instead we have to close 
        # the repl with CTRL-D.
        sys.exit(1) 
    except asyncio.CancelledError:
        # task is cancelled on disconnect, so we ignore this error
        pass
    except asyncio.TimeoutError:
        print("timeout happened")     
    except RuntimeError:
        print("runtime error")    



###################################################################################################
#         background info about windows clean shutdown of program 
###################################################################################################
#
# https://stackoverflow.com/questions/35772001/how-to-handle-a-signal-sigint-on-a-windows-os-machine/35792192#35792192
#
#     Windows adds the non-standard SIGBREAK. Both console and non-console processes can raise these
#     signals, but only a console process can receive them from another process. The CRT implements
#     this by registering a console control event handler via SetConsoleCtrlHandler.
#
#     The console sends a control event by creating a new thread in an attached process that begins
#     executing at CtrlRoutine in kernel32.dll or kernelbase.dll (undocumented). That the handler
#     doesn't execute on the main thread can lead to synchronization problems.
#
#     When the console sends the process a CTRL_C_EVENT or CTRL_BREAK_EVENT, the CRT's handler calls
#     the registered SIGINT or SIGBREAK handler, respectively. The SIGBREAK handler is also called
#     for the CTRL_CLOSE_EVENT that the console sends when its window is closed. Python defaults to
#     handling SIGINT by rasing a KeyboardInterrupt in the main thread. However, SIGBREAK is
#     initially the default CTRL_BREAK_EVENT handler, which calls ExitProcess(STATUS_CONTROL_C_EXIT).
#
#
#     CTRL_BREAK_EVENT is all you can depend on since it can't be disabled. Sending this event is a
#     simple way to gracefully kill a child process that was started with CREATE_NEW_PROCESS_GROUP,
#     assuming it has a Windows CTRL_BREAK_EVENT or C SIGBREAK handler. If not, the default handler
#     will terminate the process
#
# https://www.mail-archive.com/search?l=python-list@python.org&q=subject:%22Avoid+nested+SIGINT+handling%22&o=newest&f=1
#
#     But Python's C signal handler just sets a flag
#     and returns, so SIGBREAK due to those events can't be handled with
#     Python's signal module. The process gets terminated long before the
#     main thread can call Python's registered SIGBREAK handler.
#
# https://stackoverflow.com/questions/54538689/execute-python-instruction-when-console-is-closed#comment95890802_54538689
#
#     When the console is closed, all attached processes are sent a CTRL_CLOSE_EVENT. This gets
#     mapped to SIGBREAK in Python, but it can't be handled due to the way Python's signal handler
#     is designed (i.e. set a flag and return immediately). For the close event, the session server
#     (csrss.exe) gives a client process 5 seconds to return. After the client either returns or
#     times out, it is forcefully terminated, unless it already exited on its own. To handle this
#     event, you'll need to use ctypes to set your own console control handler via
#     SetConsoleCtrlHandler to register a ctypes callback.
#
#     => handling sigbreak with signal module does not work
#     instead use pywin32 module with method  win32api.SetConsoleCtrlHandler
