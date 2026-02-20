Worker Threads
==============

Workers run in QThread subthreads.  They communicate with the main thread
exclusively via Qt signals — never by accessing shared mutable state directly.

.. automodule:: cangui.worker_can_receiver
   :members:
   :undoc-members: False
   :show-inheritance:

.. automodule:: cangui.worker_can_transmitter
   :members:
   :undoc-members: False
   :show-inheritance:

.. automodule:: cangui.worker_trace_player
   :members:
   :undoc-members: False
   :show-inheritance:

.. automodule:: cangui.worker_uds
   :members:
   :undoc-members: False
   :show-inheritance:
