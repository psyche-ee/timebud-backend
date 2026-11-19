"""Record/DTR endpoints mirroring Laravel's RecordController."""

from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from config.api.responses import error_response, success_response
from . import services
from .serializers import RecordSerializer


class TimeInView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        record, error = services.clock_in(request.user)
        if error:
            return error_response(message=error, data=None)
        return success_response(
            data=RecordSerializer(record).data,
            message="Clocked in successfully",
        )


class TimeOutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        record, error = services.clock_out(request.user)
        if error:
            return error_response(message=error, data=None)
        return success_response(
            data=RecordSerializer(record).data,
            message="Clocked out successfully",
        )


class ManualTimeInView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
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
        record, error = services.manual_time_out(
            request.user, request.data.get("timestamp")
        )
        if error:
            return error_response(message=error)
        return success_response(
            data=RecordSerializer(record).data,
            message="Manual time-out recorded",
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
