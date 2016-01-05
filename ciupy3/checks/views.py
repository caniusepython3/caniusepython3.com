from django.http import Http404
from django.shortcuts import redirect
from django.utils.encoding import force_text

from rest_framework import generics
from rest_framework.decorators import api_view, renderer_classes
from rest_framework.generics import get_object_or_404
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response

from vanilla import CreateView

from .forms import CheckForm
from .tasks import (run_check, get_compatible, get_total,
                    get_checked, real_project_name)
from .models import Check, Project, get_redis
from .serializers import PublicCheckSerializer, ProjectSerializer


redis = get_redis()


class CheckDetailView(generics.RetrieveAPIView):
    queryset = Check.objects.all()
    serializer_class = PublicCheckSerializer
    lookup_field = 'pk'
    template_name = 'checks/check_detail.html'

    def post(self, request, *args, **kwargs):
        return self.retrieve(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        self.object = self.get_object()
        # redirect if the case doesn't match
        if self.request.data.get('check', None) == 'again':
            self.object.finished_at = None
            self.object.save()
            run_check.delay(self.object.pk)
            return redirect(self.object)
        serializer = self.get_serializer(self.object)
        return Response(serializer.data)


class CheckCreateView(CreateView):
    form_class = CheckForm
    model = Check

    def get_form(self, data=None, files=None, **kwargs):
        projects = self.request.GET.get('projects', None)
        if projects is not None:
            kwargs['initial'] = {'requirements': '\n'.join(projects.split())}
        return super(CheckCreateView, self).get_form(data, files, **kwargs)

    def form_valid(self, form):
        requirements = form.cleaned_data['requirements']
        if len(requirements) == 1:
            requested_name = requirements[0]
            name = real_project_name(requested_name)
            if name is not None:
                return redirect('project-detail', name=name)
        check = form.save()
        run_check.delay(check.pk)
        return redirect(check)

    def get_context_data(self, *args, **kwargs):
        context = super(CheckCreateView, self).get_context_data(*args,
                                                                **kwargs)
        context.update({
            'compatible': get_compatible(),
            'total': get_total(),
            'checked': get_checked(),
        })
        return context


class ProjectDetailView(generics.RetrieveAPIView):
    queryset = Project.objects.all()
    serializer_class = ProjectSerializer
    lookup_field = 'name'
    template_name = 'projects/project_detail.html'

    def post(self, request, *args, **kwargs):
        return self.retrieve(request, *args, **kwargs)

    def get_object(self, queryset=None):
        if queryset is None:
            queryset = self.filter_queryset(self.get_queryset())

        lookup_url_kwarg = self.lookup_url_kwarg or self.lookup_field
        self.lookup = self.kwargs.get(lookup_url_kwarg, None)
        filter_kwargs = {self.lookup_field + '__iexact': self.lookup}
        try:
            project = get_object_or_404(queryset, **filter_kwargs)
        except Http404:
            name = real_project_name(self.lookup)
            if name is None:
                raise
            project, created = Project.objects.get_or_create(name=name)
            project.run_check()

        return project

    def retrieve(self, request, *args, **kwargs):
        self.object = self.get_object()
        # redirect if the case doesn't match
        if self.object.name != self.lookup:
            return redirect(self.object)

        if self.request.data.get('check', None) == 'again':
            self.object.run_check()
            return redirect(self.object)

        serializer = self.get_serializer(self.object)
        return Response(serializer.data)


@api_view(['GET'])
@renderer_classes([JSONRenderer])
def autocomplete(request):
    term = request.query_params.get('term', '')
    count = 50
    try:
        count = int(request.query_params.get('count', count))
    except ValueError:
        pass
    if count > 50:
        count = 50
    byte_results = redis.zrangebylex('autocomplete',
                                     u'[%s' % term, u'[%s\xff' % term)
    text_results = (force_text(result).split(':')[1]
                    for result in byte_results)
    return Response(sorted(text_results, key=len)[:count])
