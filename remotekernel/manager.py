"""A kernel manager with a tornado IOLoop"""

#-----------------------------------------------------------------------------
#  Copyright (C) 2013  The IPython Development Team
#
#  Distributed under the terms of the BSD License.  The full license is in
#  the file COPYING, distributed as part of this software.
#-----------------------------------------------------------------------------

#-----------------------------------------------------------------------------
# Imports
#-----------------------------------------------------------------------------

from __future__ import absolute_import

import os
import socket
from subprocess import Popen, PIPE

from zmq.eventloop import ioloop
from zmq.eventloop.zmqstream import ZMQStream

from traitlets import Instance
from jupyter_client.localinterfaces import is_local_ip, local_ips

from jupyter_client.manager import KernelManager
from jupyter_client.ioloop.restarter import IOLoopKernelRestarter

#-----------------------------------------------------------------------------
# Code
#-----------------------------------------------------------------------------


def as_zmqstream(f):
    def wrapped(self, *args, **kwargs):
        socket = f(self, *args, **kwargs)
        return ZMQStream(socket, self.loop)
    return wrapped

class RemoteIOLoopKernelManager(KernelManager):

    loop = Instance('zmq.eventloop.ioloop.IOLoop', allow_none=False)
    # _restarter = Instance('remotekernel.restarter.RemoteIOLoopKernelRestarter')

    def _loop_default(self):
        return ioloop.IOLoop.instance()

    def start_kernel(self, **kw):
        """Starts a kernel on this host in a separate process.

        If random ports (port=0) are being used, this method must be called
        before the channels are created.

        Parameters
        ----------
        **kw : optional
             keyword arguments that are passed down to build the kernel_cmd
             and launching the kernel (e.g. Popen kwargs).
        """
        self.host = self.kernel_spec.host
        # allow a different username in host
        if '@' in self.host:
            self.ip = socket.gethostbyname(self.kernel_spec.host.split('@')[1])
        else:
            self.ip = socket.gethostbyname(self.kernel_spec.host)

        # write connection file / get default ports
        self.write_connection_file()

        # save kwargs for use in restart
        self._launch_args = kw.copy()
        # build the Popen cmd
        extra_arguments = kw.pop('extra_arguments', [])
        # build kernel_cmd; we will overwrite the connection_file arg below
        kernel_cmd = self.format_kernel_cmd(extra_arguments=extra_arguments)

        # debug stuff
        # This may be OSX only. It ensures passwordless login works.

        # decide where to copy the connection file on the remote host
        get_remote_home_ = ['ssh', self.host, 'echo', '$HOME']
        if os.name == 'nt':
            get_remote_home_ = ' '.join(get_remote_home_)

        get_remote_home = Popen(get_remote_home_, stdin=PIPE, stdout=PIPE)
        if get_remote_home.wait() != 0:
            raise RuntimeError("Failed to connect to remote host {0}"
                               "".format(self.ip))
        result, = get_remote_home.stdout.readlines()
        remote_home = result.decode()[:-1]
        remote_connection_file = os.path.join(
                remote_home, '.ipython', 'kernels',
                os.path.basename(self.connection_file)).replace('\\', '/')

        # copy the connection file to the remote machine
        remote_connection_file_dir = os.path.dirname(remote_connection_file).replace('\\', '/')
        mkdirp_ = ['ssh', self.host, 'mkdir', '-p', remote_connection_file_dir]
        if os.name == 'nt':
            mkdirp_ = ' '.join(mkdirp_)
        mkdirp = Popen(mkdirp_)
        if mkdirp.wait() != 0:
            raise RuntimeError("Failed to create directory for connection "
                               "file on remote host {0}".format(self.ip))
        transfer_ = ['scp', ('"' + self.connection_file + '"').replace('\\', '/').replace('C:', '/c'),
                          '{0}:{1}'.format(self.host, remote_connection_file)]
        if os.name == 'nt':
            transfer_ = ' '.join(transfer_)                          
        transfer = Popen(transfer_)
        if transfer.wait() != 0:
            raise RuntimeError("Failed to copy connection file to host {0}"
                               "".format(self.ip))

        # launch the kernel subprocess
        kernel_cmd[1 + kernel_cmd.index('-f')] = remote_connection_file
        # if len(kernel_cmd) > 6:
        #     kernel_cmd[6] = remote_profile_dir = os.path.join(remote_connection_file_dir, '..', 'profile_default')
        kernel_cmd.append('--debug')
        kernel_cmd_ = ['ssh', self.host,
                             '{0}'.format(' '.join(kernel_cmd))]
        if os.name == 'nt':
            kernel_cmd_ = ' '.join(kernel_cmd_)                               
        self.kernel = Popen(kernel_cmd_,
                            stdout=PIPE, stdin=PIPE, env=os.environ)
        # self.start_restarter()
        self._connect_control_socket()

    _restarter = Instance('jupyter_client.ioloop.IOLoopKernelRestarter', allow_none=True)

    def start_restarter(self):
        if self.autorestart and self.has_kernel:
            if self._restarter is None:
                self._restarter = IOLoopKernelRestarter(
                    kernel_manager=self, loop=self.loop,
                    parent=self, log=self.log
                )
            self._restarter.start()

    def stop_restarter(self):
        if self.autorestart:
            if self._restarter is not None:
                self._restarter.stop()

    connect_shell = as_zmqstream(KernelManager.connect_shell)
    connect_iopub = as_zmqstream(KernelManager.connect_iopub)
    connect_stdin = as_zmqstream(KernelManager.connect_stdin)
    connect_hb = as_zmqstream(KernelManager.connect_hb)
