"""Keys the agent loop sets in runtime-event ``data`` for the surfaces that render it."""

#: Marks a ``message_update`` that carries one streamed piece of a reply. The
#: complete text still follows in an unmarked ``message_update``.
PARTIAL_MESSAGE_KEY = "partial"
