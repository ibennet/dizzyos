"""A minimal GTFS-realtime reader — just the fields the transit apps need.

The MTA publishes realtime arrivals (subway and bus alike) as protobuf. Rather
than take on a protobuf dependency and generated bindings for a schema we use
three fields of, this walks the wire format directly and picks out those fields
by number. Field numbers are from the GTFS-realtime spec:

    FeedMessage.entity = 2          FeedEntity.trip_update = 3
    TripUpdate.trip = 1             TripUpdate.stop_time_update = 2
    TripDescriptor.route_id = 5
    StopTimeUpdate.arrival = 2, .departure = 3, .stop_id = 4
    StopTimeEvent.time = 2

Everything else in the message is skipped by length. Malformed or truncated data
yields fewer arrivals rather than raising — a bad feed shouldn't blank the sign.
"""

# Protobuf wire types we care about; anything else is skipped by fixed width.
_VARINT = 0
_LEN = 2


def _varint(buf, i, end):
    """Decode a varint at `i`. Returns (value, next_index), or (None, end) if it
    runs off the end of the region."""
    value = shift = 0
    while i < end:
        byte = buf[i]
        i += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, i
        shift += 7
    return None, end


def walk(buf, start, end, visit):
    """Walk one length-delimited region, calling `visit(field_no, wire, value, s, e)`.

    For varint fields `value` holds the number; for length-delimited fields `s`/`e`
    bracket the payload. Stops early on anything it can't parse.
    """
    i = start
    while i < end:
        key, i = _varint(buf, i, end)
        if key is None:
            return
        field_no, wire = key >> 3, key & 0x07
        if wire == _VARINT:
            value, i = _varint(buf, i, end)
            if value is None:
                return
            visit(field_no, wire, value, 0, 0)
        elif wire == _LEN:
            length, i = _varint(buf, i, end)
            if length is None or i + length > end:
                return
            visit(field_no, wire, 0, i, i + length)
            i += length
        elif wire == 5:  # fixed32
            i += 4
        elif wire == 1:  # fixed64
            i += 8
        else:  # groups (deprecated) and unknown types — stop rather than misread
            return


def looks_like_feed(buf):
    """True if `buf` opens with a FeedHeader carrying a gtfs_realtime_version.

    The feeds are served from S3, which answers a missing key with an XML error body
    and HTTP 200 — so "it downloaded" is not the same as "it's a feed".
    """
    if len(buf) < 4:
        return False
    found = []

    def header(field_no, wire, _value, start, end):
        if found or field_no != 1 or wire != _LEN:  # FeedMessage.header
            return
        walk(buf, start, end, lambda hn, hw, *_: found.append(True) if hn == 1 and hw == _LEN else None)

    walk(buf, 0, len(buf), header)
    return bool(found)


def iter_arrivals(buf):
    """Yield `(route_id, stop_id, epoch_seconds)` for every stop-time update in the
    feed. `stop_id` is passed through untouched (subway ids keep their direction
    suffix, e.g. "F11N"; bus ids are bare codes); arrival time is used when
    present, departure as the fallback."""
    out = []

    def entity(field_no, wire, _value, start, end):
        if field_no != 2 or wire != _LEN:  # FeedMessage.entity
            return
        walk(buf, start, end, trip_update)

    def trip_update(field_no, wire, _value, start, end):
        if field_no != 3 or wire != _LEN:  # FeedEntity.trip_update
            return
        route, stop_times = [], []

        def field(tn, tw, _tv, ts, te):
            if tn == 1 and tw == _LEN:  # TripUpdate.trip
                walk(buf, ts, te, lambda dn, dw, _dv, ds, de:
                     route.append(_text(buf, ds, de)) if dn == 5 and dw == _LEN else None)
            elif tn == 2 and tw == _LEN:  # TripUpdate.stop_time_update
                stop_times.append(_stop_time(buf, ts, te))

        walk(buf, start, end, field)
        if not route:
            return
        for stop_id, when in stop_times:
            if stop_id and when:
                out.append((route[0], stop_id, when))

    walk(buf, 0, len(buf), entity)
    return out


def _stop_time(buf, start, end):
    """Extract (stop_id, epoch_seconds) from one StopTimeUpdate."""
    stop_id, when = "", 0

    def field(field_no, wire, _value, s, e):
        nonlocal stop_id, when
        if field_no == 4 and wire == _LEN:  # stop_id
            stop_id = _text(buf, s, e)
        elif field_no in (2, 3) and wire == _LEN and not when:  # arrival, else departure
            walk(buf, s, e, _time)

    def _time(field_no, wire, value, _s, _e):
        nonlocal when
        if field_no == 2 and wire == _VARINT and not when:  # StopTimeEvent.time
            when = value

    walk(buf, start, end, field)
    return stop_id, when


def _text(buf, start, end):
    """Decode a protobuf string. Route and stop ids are ASCII; anything odd is
    dropped rather than raising."""
    return bytes(buf[start:end]).decode("ascii", "ignore")
