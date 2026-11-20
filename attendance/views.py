"""Record/DTR endpoints mirroring Laravel's RecordController."""

from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from config.api.responses import error_response, success_response
from config.api.serialization import parse_timestamp
from . import intelligence, services
from .serializers import RecordSerializer


def _punch_guard(request, action):
    """Block an anomalous punch until the client acknowledges its warnings."""
    raw = request.data.get("timestamp")
    payload = {"timestamp": str(raw) if raw is not None else None}

    def detector():
        timestamp = None
        if raw:
            try:
                timestamp = parse_timestamp(raw)
            except ValueError:
                timestamp = None
        return intelligence.detect_anomalies(
            request.user,
            action=action,
            timestamp=timestamp,
            explicit_timestamp=bool(raw),
        )

    return intelligence.guard_anomalies(request, request.user, action, payload, detector)


class TimeInView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        guard = _punch_guard(request, "time-in")
        if guard:
            return guard
        record, error = services.clock_in(request.user, request.data.get("timestamp"))
        if error:
            return error_response(message=error, data=None)
        return success_response(
            data=RecordSerializer(record).data,
            message="Clocked in successfully",
        )


class TimeOutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        guard = _punch_guard(request, "time-out")
        if guard:
            return guard
        record, error = services.clock_out(request.user, request.data.get("timestamp"))
        if error:
            return error_response(message=error, data=None)
        return success_response(
            data=RecordSerializer(record).data,
            message="Clocked out successfully",
        )


class ManualTimeInView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        guard = _punch_guard(request, "manual-time-in")
        if guard:
            return guard
        record, error = services.manual_time_in(
            request.user, request.data.get("timestamp")
        )
        if error:
            return error_response(message=error)
        return success_response(
            data=RecordSerializer(record).data,
            message="Manual time-in recorded",
        )


class ManualTimeOutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        guard = _punch_guard(request, "manual-time-out")
        if guard:
            return guard
        record, error = services.manual_time_out(
            request.user, request.data.get("timestamp")
        )
        if error:
            return error_response(message=error)
        return success_response(
            data=RecordSerializer(record).data,
            message="Manual time-out recorded",
        )


class ManualEntryParseView(APIView):
    """Interpret free text and preview the hours/pay it would produce."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        if request.data.get("kind") == "punch":
            return self._parse_punch(request)

        parsed = intelligence.parse_manual_entry(request.data.get("text"))
        if parsed is None:
            return error_response(
                message="Could not understand that entry. Try "
                "'8:30 AM-6:15 PM last Tuesday, 30m break'."
            )

        try:
            time_in = parse_timestamp(parsed["time_in"])
            time_out = parse_timestamp(parsed["time_out"])
        except ValueError:
            return error_response(message="Invalid time range")

        payload = {
            **parsed,
            "preview": intelligence.compute_preview(
                request.user, time_in, time_out, parsed["break_minutes"]
            ),
            "warnings": intelligence.detect_anomalies(
                request.user,
                action="manual-entry",
                time_in=time_in,
                time_out=time_out,
                break_minutes=parsed["break_minutes"],
            ),
        }
        return success_response(data=payload, message="Entry interpreted")

    def _parse_punch(self, request):
        parsed = intelligence.parse_manual_punch(request.data.get("text"))
        if parsed is None:
            return error_response(
                message="Could not understand that time. Try '8:30 AM last Tuesday'."
            )
        try:
            timestamp = parse_timestamp(parsed["timestamp"])
        except ValueError:
            return error_response(message="Invalid time")

        action = (
            "manual-time-in"
            if request.data.get("type") == "time-in"
            else "manual-time-out"
        )
        return success_response(
            data={
                **parsed,
                "warnings": intelligence.detect_anomalies(
                    request.user,
                    action=action,
                    timestamp=timestamp,
                    explicit_timestamp=True,
                ),
            },
            message="Time interpreted",
        )


class ManualEntryView(APIView):
    """Save a complete described session (punches + break) atomically."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        raw_in = request.data.get("time_in")
        raw_out = request.data.get("time_out")
        if not raw_in or not raw_out:
            return error_response(message="Time in and time out are required")

        try:
            break_minutes = int(request.data.get("break_minutes") or 0)
            time_in = parse_timestamp(raw_in)
            time_out = parse_timestamp(raw_out)
        except (TypeError, ValueError):
            return error_response(message="Invalid time range")

        payload = {
            "time_in": str(raw_in),
            "time_out": str(raw_out),
            "break_minutes": break_minutes,
        }
        guard = intelligence.guard_anomalies(
            request,
            request.user,
            "manual-entry",
            payload,
            lambda: intelligence.detect_anomalies(
                request.user,
                action="manual-entry",
                time_in=time_in,
                time_out=time_out,
                break_minutes=break_minutes,
            ),
        )
        if guard:
            return guard

        record, error = services.create_manual_session(
            request.user, raw_in, raw_out, break_minutes
        )
        if error:
            return error_response(message=error)
        return success_response(
            data={
                **RecordSerializer(record).data,
                "preview": intelligence.compute_preview(
                    request.user, time_in, time_out, break_minutes
                ),
            },
            message="Manual session recorded",
        )


class BreakInView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        break_row, error = services.start_break(request.user, request.data.get("timestamp"))
        if error:
            return error_response(message=error, data=None)
        return success_response(
            data={"id": break_row.id, "time_in": break_row.time_in.isoformat()},
            message="Break started",
        )


class BreakOutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        break_row, error = services.end_break(request.user, request.data.get("timestamp"))
        if error:
            return error_response(message=error, data=None)
        return success_response(
            data={
                "id": break_row.id,
                "time_out": break_row.time_out.isoformat(),
                "duration_hrs": str(break_row.duration_hrs),
            },
            message="Break ended",
        )


class RecordsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return success_response(
            data=services.get_records(request.user),
            message="Records retrieved",
        )


class DeleteRecordView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, pk):
        error = services.delete_record(request.user, pk)
        if error:
            return error_response(message=error)
        return success_response(message="Record deleted successfully")


class EarningsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return success_response(
            data=services.get_earnings(request.user),
            message="Earnings retrieved",
        )


class DashboardView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return success_response(
            data=services.get_dashboard(request.user),
            message="Dashboard data retrieved",
        )
