from .models import Organization


def all_orgs(request):
    if request.user.is_authenticated:
        return {'all_orgs': Organization.objects.all().order_by('name')}
    return {'all_orgs': []}
