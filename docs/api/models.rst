Qt Item Models
==============

All models live on the main (GUI) thread.  They accumulate incoming messages
in a ``_pending`` list and drain it on a QTimer to decouple high-frequency CAN
traffic from the UI render rate.

.. automodule:: cangui.model_rx_message
   :members:
   :undoc-members: False
   :show-inheritance:

.. automodule:: cangui.model_tx_message
   :members:
   :undoc-members: False
   :show-inheritance:

.. automodule:: cangui.model_trace
   :members:
   :undoc-members: False
   :show-inheritance:

.. automodule:: cangui.model_watch
   :members:
   :undoc-members: False
   :show-inheritance:

.. automodule:: cangui.model_plot_list
   :members:
   :undoc-members: False
   :show-inheritance:

.. automodule:: cangui.model_rx_filter
   :members:
   :undoc-members: False
   :show-inheritance:

.. automodule:: cangui.model_connection
   :members:
   :undoc-members: False
   :show-inheritance:

.. automodule:: cangui.model_database
   :members:
   :undoc-members: False
   :show-inheritance:

.. automodule:: cangui.model_project
   :members:
   :undoc-members: False
   :show-inheritance:
