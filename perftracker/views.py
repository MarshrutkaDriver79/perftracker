# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import datetime
import json
import http.client

from django.views.generic.base import TemplateView
from django.template import RequestContext
from django.template.response import TemplateResponse
from django.contrib import messages
from django.http import HttpResponse, HttpResponseRedirect, HttpResponseBadRequest, JsonResponse, Http404
from django.core import serializers
from django.views.decorators.csrf import csrf_exempt
from django.core.serializers import serialize
from django.core.exceptions import SuspiciousOperation, ObjectDoesNotExist
from django.contrib.auth.decorators import login_required
from django_datatables_view.base_datatable_view import BaseDatatableView
from django.db.models import Q
from django.db.models import Count
from django.db.models.query import QuerySet

from perftracker.models.project import ProjectModel
from perftracker.models.comparison import ComparisonModel, ComparisonSimpleSerializer, ComparisonNestedSerializer, ptComparisonServSideView
from perftracker.models.regression import RegressionModel, RegressionSerializer, ptRegressionServSideView
from perftracker.models.comparison import ptCmpTableType, ptCmpChartType
from perftracker.models.job import JobModel, JobSimpleSerializer, JobNestedSerializer
from perftracker.models.hw_farm_node import HwFarmNodeModel, HwFarmNodeSimpleSerializer, HwFarmNodeNestedSerializer, HwFarmNodesTimeline
from perftracker.models.test import TestModel, TestSimpleSerializer, TestDetailedSerializer
from perftracker.models.test_group import TestGroupModel, TestGroupSerializer
from perftracker.models.env_node import EnvNodeModel

from perftracker.forms import ptCmpDialogForm
from perftracker.helpers import pt_dur2str

API_VER = 1.0


def ptHandle500(request):
    context = RequestContext(request)
    return HttpResponseBadRequest(context)


def ptRedirect(request):
    redirect = request.session.get('redirect', None)
    del request.session['redirect']
    if redirect:
        return HttpResponseRedirect(redirect)
    return HttpResponse()

# HTML views ###


def ptBaseHtml(request, project_id, template, params=None, obj=None):
    if request.method == 'POST':
        raise Http404

    params = params if params else {}

    dentries = request.path.split("/")
    verb = dentries[2] if len(dentries) > 3 else "comparison"

    default_params = {'projects': ProjectModel().ptGetAll(),
                      'request': request,
                      'verb': verb,
                      'obj': obj,
                      'project': ProjectModel.ptGetById(project_id),
                      'api_ver': API_VER,
                      'screens': [('Home', '/%d/home/' % project_id),
                                  ('Regressions', '/%d/regression/' % project_id),
                                  ('Comparisons', '/%d/comparison/' % project_id),
                                  ('Jobs', '/%d/job/' % project_id),
                                  ('Hosts', '/%d/hw_farm/' % project_id)]}
    params.update(default_params)
    return TemplateResponse(request, template, params)


def ptHomeHtml(request, project_id):
    return ptBaseHtml(request, project_id, 'home.html')


def ptComparisonAllHtml(request, project_id):
    return ptBaseHtml(request, project_id, 'comparison_all.html')


def ptComparisonIdHtml(request, project_id, cmp_id):
    try:
        obj = ComparisonModel.objects.get(pk=cmp_id)
    except ComparisonModel.DoesNotExist:
        raise Http404

    # register 'range' template tag

    return ptBaseHtml(request, project_id, 'comparison_id.html',
                      params={'jobs': obj.jobs.all(),
                              'cmp_view': ptComparisonServSideView(obj),
                              'ptCmpChartType': ptCmpChartType,
                              'ptCmpTableType': ptCmpTableType
                              },
                      obj=obj)


def ptRegressionAllHtml(request, project_id):
    return ptBaseHtml(request, project_id, 'regression_all.html')


def ptRegressionIdHtml(request, project_id, regression_id):
    try:
        obj = RegressionModel.objects.get(pk=regression_id)
    except RegressionModel.DoesNotExist:
        raise Http404

    # register 'range' template tag

    return ptBaseHtml(request, project_id, 'regression_id.html',
                      params={'jobs': obj.ptGetJobs(),
                              'first_job': obj.first_job,
                              'last_job': obj.last_job,
                              'duration': pt_dur2str(obj.last_job.end - obj.first_job.end),
                              'regr_view': ptRegressionServSideView(obj),
                              'ptCmpChartType': ptCmpChartType,
                              'ptCmpTableType': ptCmpTableType,
                              },
                      obj=obj)


# @login_required
def ptJobAllHtml(request, project_id):
    params = {'cmp_form': ptCmpDialogForm()}
    return ptBaseHtml(request, project_id, 'job_all.html', params=params)


# @login_required
def ptJobIdHtml(request, project_id, job_id):
    try:
        obj = JobModel.objects.get(pk=job_id)
    except JobModel.DoesNotExist:
        raise Http404
    return ptBaseHtml(request, project_id, 'job_id.html', obj=obj)


# @login_required
def ptHwFarmAllHtml(request, project_id):
    params = {'timeline': HwFarmNodesTimeline(project_id).gen_html()}
    return ptBaseHtml(request, project_id, 'hw_farm_node_all.html', params=params)


# @login_required
def ptHwFarmIdHtml(request, project_id, id):
    return ptBaseHtml(request, project_id, 'hw_farm_node_id.html')


# Json views ###


def ptHomeJson(request, api_ver, project_id):
    return JsonResponse([], safe=False)



@csrf_exempt
def ptJobAllJson(request, api_ver, project_id):

    class JobJson(BaseDatatableView):
        # The model we're going to show
        model = JobModel

        # define the columns that will be returned
        columns = ['', 'id', 'end', 'env_node', 'suite_ver', 'title', 'duration', 'tests_total', 'tests_completed', 'tests_failed']

        # define column names that will be used in sorting
        # order is important and should be same as order of columns
        # displayed by datatables. For non sortable columns use empty
        # value like ''
        order_columns = ['', 'id', 'end', 'env_node', 'suite_ver', 'title', 'duration', 'tests_total', 'tests_completed', 'tests_failed']

        # set max limit of records returned, this is used to protect our site if someone tries to attack our site
        # and make it return huge amount of data
        max_display_length = 5000

        def render_column(self, row, column):
            # We want to render user as a custom column
            if column == 'tests_total':
                return '{0} {1}'.format(row.tests_total, row.tests_completed)
            else:
                return super(JobJson, self).render_column(row, column)

        def filter_queryset(self, qs):
            # use parameters passed in GET request to filter queryset

            # simple example:
            search = self.request.GET.get(u'search[value]', None)
            if search:
                qs = qs.filter(Q(title__icontains=search) | Q(suite_ver__icontains=search))

            if project_id != 0:
                qs = qs.filter(Q(project_id=project_id))

            qs = qs.filter(Q(deleted=False))

            # more advanced example using extra parameters
            # filter_title = self.request.GET.get(u'title', None)
            #
            # if filter_title:
            #    title_parts = filter_title.split(' ')
            #    qs_params = None
            #    for part in title_parts:
            #        q = Q(title__istartswith=part)|Q(title__istartswith=part)
            #        qs_params = qs_params | q if qs_params else q
            #    qs = qs.filter(qs_params)
            return qs

        def prepare_results(self, qs):
            return JobSimpleSerializer(qs, many=True).data

    if request.method == 'POST':
        body_unicode = request.body.decode('utf-8')
        body = json.loads(body_unicode)

        try:
            JobModel.ptValidateJson(body)
        except SuspiciousOperation as e:
            return HttpResponseBadRequest(e)

        uuid = body.get('uuid', None)
        append = body.get('append', False)
        replace = body.get('replace', False)
        job = None
        if uuid:
            try:
                job = JobModel.objects.get(uuid=uuid)
            except JobModel.DoesNotExist:
                pass

        if not job:
            if replace:
                return HttpResponseBadRequest("Job with uuid %s doesn't exist" % uuid)
            job = JobModel(title=body['job_title'], uuid=body['uuid'])

        job.ptUpdate(body)
        return HttpResponse("OK")

    return JobJson.as_view()(request)


def ptJobIdJson(request, api_ver, project_id, job_id):
    try:
        return JsonResponse(JobNestedSerializer(JobModel.objects.get(pk=job_id)).data, safe=False)
    except JobModel.DoesNotExist:
        return JsonResponse([], safe=False)


def ptJobTestAllJson(request, api_ver, project_id, job_id, group_id):
    class TestView(BaseDatatableView):
        model = TestModel
        columns = ['', 'id', 'seq_num', 'tag', 'category', 'duration', 'avg_score', 'avg_plusmin']
        order_columns = ['', 'id', 'seq_num', 'tag', 'category', 'duration', 'avg_score', 'avg_plusmin']
        max_display_length = 5000

        def filter_queryset(self, qs):
            # use parameters passed in GET request to filter queryset

            qs = qs.filter(job=job_id)

            gid = int(group_id)
            if gid:
                try:
                    group = TestGroupModel.objects.get(pk=gid)
                    qs = qs.filter(job=job_id).filter(group=group.tag)
                except TestGroupModel.DoesNotExist:
                    qs = TestModel.objects.none()

            search = self.request.GET.get(u'search[value]', None)
            if search:
                qs = qs.filter(Q(tag__icontains=search) | Q(category__icontains=search))
            return qs

        def prepare_results(self, qs):
            return TestSimpleSerializer(qs, many=True).data

    return TestView.as_view()(request)


@csrf_exempt
def ptJobGroupAllJson(request, api_ver, project_id, job_id):
    try:
        job = JobModel.objects.get(pk=job_id)
    except JobModel.DoesNotExist:
        raise Http404

    groups = TestModel.objects.filter(job=job).values('group').annotate(Count('id'))

    ret = []
    for g in groups:
        ret.append(TestGroupModel.ptGetByTag(g['group']))
    return JsonResponse(TestGroupSerializer(ret, many=True).data, safe=False)


def ptJobTestIdJson(request, api_ver, project_id, job_id, group_id, test_id):
    try:
        return JsonResponse(TestDetailedSerializer(TestModel.objects.get(id=test_id)).data, safe=False)
    except TestModel.DoesNotExist:
        return JsonResponse([], safe=False)


@csrf_exempt
def ptComparisonAllJson(request, api_ver, project_id):
    class ComparisonJson(BaseDatatableView):
        # The model we're going to show
        model = ComparisonModel

        # define the columns that will be returned
        columns = ['', 'id', 'updated', 'env_node', 'suite_ver', 'title', 'jobs', 'tests_total', 'tests_completed', 'tests_failed']

        # define column names that will be used in sorting
        # order is important and should be same as order of columns
        # displayed by datatables. For non sortable columns use empty
        # value like ''
        order_columns = ['', 'id', 'updated', 'env_node', 'suite_ver', 'title', 'jobs', 'tests_total', 'tests_completed', 'tests_failed']

        # set max limit of records returned, this is used to protect our site if someone tries to attack our site
        # and make it return huge amount of data
        max_display_length = 5000

        def render_column(self, row, column):
            # We want to render user as a custom column
            if column == 'tests_total':
                return '{0} {1}'.format(row.tests_total, row.tests_completed)
            else:
                return super(JobJson, self).render_column(row, column)

        def filter_queryset(self, qs):
            # use parameters passed in GET request to filter queryset

            # simple example:
            search = self.request.GET.get(u'search[value]', None)
            if search:
                qs = qs.filter(Q(title__icontains=search) | Q(suite_ver__icontains=search))

            if project_id != 0:
                qs = qs.filter(Q(project_id=project_id))

            qs = qs.filter(Q(deleted=False))

            return qs

        def prepare_results(self, qs):
            return ComparisonSimpleSerializer(qs, many=True).data

    if request.method == "POST":
        body_unicode = request.body.decode('utf-8')
        body = json.loads(body_unicode)

        try:
            ComparisonModel.ptValidateJson(body)
        except SuspiciousOperation as e:
            return HttpResponseBadRequest(e)

        cmp = ComparisonModel(project=ProjectModel.ptGetById(project_id))
        try:
            cmp.ptUpdate(body)
        except SuspiciousOperation as e:
            return HttpResponseBadRequest(e)

        return JsonResponse({'id': cmp.id}, safe=False)

    return ComparisonJson.as_view()(request)


def ptComparisonIdJson(request, api_ver, project_id, cmp_id):
    try:
        return JsonResponse(ComparisonNestedSerializer(ComparisonModel.objects.get(pk=cmp_id)).data, safe=False)
    except ComparisonModel.DoesNotExist:
        return JsonResponse([], safe=False)


def ptComparisonTestAllJson(request, api_ver, project_id, cmp_id, group_id):
    cmp = ComparisonModel.objects.get(pk=cmp_id)
    jobs = cmp.jobs.all()
    ret = []
    for job in jobs:
        ret.append(ptJobTestAllJson(request, api_ver, project_id, job.id, group_id).content.decode("utf-8"))
    return HttpResponse("[" + ",".join(ret) + "]", content_type="application/json")


@csrf_exempt
def ptComparisonGroupAllJson(request, api_ver, project_id, cmp_id):
    try:
        cmp = ComparisonModel.objects.get(pk=cmp_id)
    except ComparisonModel.DoesNotExist:
        raise Http404

    jobs = cmp.jobs.all()

    found = set()
    ret = []
    for job in jobs:
        groups = TestModel.objects.filter(job=job).values('group').annotate(Count('id'))
        for g in groups:
            group = g['group']
            if group in found:
                continue
            found.add(group)
            ret.append(TestGroupModel.ptGetByTag(group))
    return JsonResponse(TestGroupSerializer(ret, many=True).data, safe=False)


def ptComparisonTestIdJson(request, api_ver, project_id, cmp_id, group_id, test_id):
    cmp = ComparisonModel.objects.get(pk=cmp_id)

    try:
        orig_test = TestModel.objects.get(id=test_id)
    except TestModel.DoesNotExist:
        return JsonResponse([], safe=False)

    jobs = cmp.jobs.all()
    ret = []
    for job in jobs:
        try:
            test = TestModel.objects.get(job=job.id, tag=orig_test.tag, category=orig_test.category)
            ret.append(TestDetailedSerializer(test).data)
        except TestModel.DoesNotExist:
            ret.append({})
    return JsonResponse(ret, safe=False)


@csrf_exempt
def ptRegressionAllJson(request, api_ver, project_id):
    class RegressionJson(BaseDatatableView):
        # The model we're going to show
        model = RegressionModel

        # define the columns that will be returned
        columns = ['', 'id', 'title', 'tag', 'jobs', 'first_job', 'last_job']

        # define column names that will be used in sorting
        # order is important and should be same as order of columns
        # displayed by datatables. For non sortable columns use empty
        # value like ''
        order_columns = ['', 'id', 'title', 'tag', 'jobs', 'first_job', 'last_job']

        # set max limit of records returned, this is used to protect our site if someone tries to attack our site
        # and make it return huge amount of data
        max_display_length = 5000

        def render_column(self, row, column):
            # We want to render user as a custom column
            if column == 'tests_total':
                return '{0} {1}'.format(row.tests_total, row.tests_completed)
            else:
                return super(JobJson, self).render_column(row, column)

        def filter_queryset(self, qs):
            # use parameters passed in GET request to filter queryset

            # simple example:
            search = self.request.GET.get(u'search[value]', None)
            if search:
                qs = qs.filter(Q(title__icontains=search) | Q(suite_ver__icontains=search))

            if project_id != 0:
                qs = qs.filter(Q(project_id=project_id))

            qs = qs.filter(Q(deleted=False))

            return qs

        def prepare_results(self, qs):
            return RegressionSerializer(qs, many=True).data

    return RegressionJson.as_view()(request)


def ptRegressionIdJson(request, api_ver, project_id, regression_id):
    try:
        return JsonResponse(RegressionSerializer(RegressionModel.objects.get(pk=regression_id)).data, safe=False)
    except RegressionModel.DoesNotExist:
        return JsonResponse([], safe=False)


def ptRegressionTestAllJson(request, api_ver, project_id, regression_id, group_id):
    regr = RegressionModel.objects.get(pk=regression_id)
    jobs = regr.jobs.all()
    ret = []
    for job in jobs:
        ret.append(ptJobTestAllJson(request, api_ver, project_id, job.id, group_id).content.decode("utf-8"))
    return HttpResponse("[" + ",".join(ret) + "]", content_type="application/json")


@csrf_exempt
def ptRegressionGroupAllJson(request, api_ver, project_id, regression_id):
    try:
        regr = RegressionModel.objects.get(pk=regression_id)
    except RegressionModel.DoesNotExist:
        raise Http404

    jobs = regr.jobs.all()

    found = set()
    ret = []
    for job in jobs:
        groups = TestModel.objects.filter(job=job).values('group').annotate(Count('id'))
        for g in groups:
            group = g['group']
            if group in found:
                continue
            found.add(group)
            ret.append(TestGroupModel.ptGetByTag(group))
    return JsonResponse(TestGroupSerializer(ret, many=True).data, safe=False)


def ptRegressionTestIdJson(request, api_ver, project_id, regression_id, group_id, test_id):
    regr = RegressionModel.objects.get(pk=regression_id)

    try:
        orig_test = TestModel.objects.get(id=test_id)
    except TestModel.DoesNotExist:
        return JsonResponse([], safe=False)

    jobs = regr.jobs.all()
    ret = []
    for job in jobs:
        try:
            test = TestModel.objects.get(job=job.id, tag=orig_test.tag, category=orig_test.category)
            ret.append(TestDetailedSerializer(test).data)
        except TestModel.DoesNotExist:
            ret.append({})
    return JsonResponse(ret, safe=False)

@csrf_exempt
def ptHwFarmNodeAllJson(request, api_ver, project_id):

    class HwFarmNodeJson(BaseDatatableView):
        # The model we're going to show
        model = HwFarmNodeModel

        # define the columns that will be returned
        columns = ['', 'order', 'id', 'name', 'os', 'hostname', 'vendor', 'model', 'cpus_count', 'ram_gb', 'storage_tb', 'network_gbs', 'locked_by']
        order_columns = ['', 'order', 'id', 'name', 'os', 'hostname', 'vendor', 'model', 'cpus_count', 'ram_gb', 'storage_tb', 'network_gbs', 'locked_by']

        # set max limit of records returned, this is used to protect our site if someone tries to attack our site
        # and make it return huge amount of data
        max_display_length = 5000

        def render_column(self, row, column):
            # We want to render user as a custom column
            if column == 'model':
                return '{0} {1}'.format(row.vendor, row.model)
            else:
                return super(HwFarmNodeJson, self).render_column(row, column)

        def filter_queryset(self, qs):
            # use parameters passed in GET request to filter queryset

            # simple example:
            search = self.request.GET.get(u'search[value]', None)
            if search:
                qs = qs.filter(Q(title__icontains=search) | Q(suite_ver__icontains=search))

            if project_id != 0:
                qs = qs.filter(Q(projects=project_id))

            qs = qs.filter(Q(hidden=False))
            return qs

        def prepare_results(self, qs):
            return HwFarmNodeSimpleSerializer(qs, many=True).data

    return HwFarmNodeJson.as_view()(request)


def ptHwFarmNodeIdJson(request, api_ver, project_id, hw_id):
    try:
        return JsonResponse(HwFarmNodeNestedSerializer(HwFarmNodeModel.objects.get(pk=hw_id)).data, safe=False)
    except HwFarmNodeModel.DoesNotExist:
        return JsonResponse([], safe=False)

