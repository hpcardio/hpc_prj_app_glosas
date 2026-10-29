"""Django screen for bank balances/statements. API credentials stay server-side."""
import json
import os
from datetime import date, datetime, timedelta, timezone
from functools import wraps
from urllib.parse import urlencode
from uuid import UUID
from django import forms
from django.http import Http404, HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone as django_timezone
from django.views.decorators.http import require_POST
from .services import ApiError, api_request

BASE='/app_glosas/openfinance'


def _request(method,path,**kwargs):
    key=os.environ.get('PRONTOGLOSA_FRONTEND_KEY','')
    response=api_request(method,path,extra_headers={'X-ProntoGlosa-Frontend-Key':key},**kwargs)
    try:return response.json()
    except ValueError:raise ApiError('Resposta inválida da consulta',502) from None
    finally:response.close()


def api_get(path,params=None):return _request('GET',path,params=params)


def api_post(path,data):return _request('POST',path,json=data)


def allowed(user):
    return user.get('perfil')=='ti' or 'contas_pagar_gestao' in (user.get('telas_permitidas') or [])


def protected(fn):
    @wraps(fn)
    def view(request,*args,**kwargs):
        if not request.session.get('api_access_token'):
            return redirect(reverse('login')+'?'+urlencode({'next':request.get_full_path()}))
        if not allowed(request.session.get('api_user') or {}):
            return HttpResponseForbidden('Acesso restrito à Gestão financeira e TI.')
        if request.is_secure() and not request.session.get('openfinance_secure_session'):
            # Reissue pre-HTTPS session cookies with the configured Secure flag.
            request.session['openfinance_secure_session']=True
        response=fn(request,*args,**kwargs)
        response['Cache-Control']='private, no-store'
        response['Referrer-Policy']='same-origin'
        return response
    return view


class OwnerForm(forms.Form):
    label=forms.CharField(label='Nome do titular',max_length=150,strip=True)
    kind=forms.ChoiceField(label='Tipo',choices=[('PJ','Pessoa jurídica'),('PF','Pessoa física')])


class PeriodForm(forms.Form):
    date_from=forms.DateField(label='De',input_formats=['%Y-%m-%d'])
    date_to=forms.DateField(label='Até',input_formats=['%Y-%m-%d'])
    def clean(self):
        data=super().clean()
        if data.get('date_from') and data.get('date_to') and data['date_from']>data['date_to']:
            raise forms.ValidationError('Confira o período: a data inicial deve ser anterior à final.')
        return data


def error_message(exc):
    if exc.status_code in {401,403}: return 'Seu acesso não foi autorizado. Entre novamente ou solicite a liberação à equipe de TI.'
    if exc.status_code==404: return 'O registro não está mais disponível. Consulte novamente.'
    if exc.status_code==409: return 'A conexão não corresponde ao titular ou ao ambiente de teste. Confira a seleção.'
    return 'Consulta temporariamente indisponível. Tente novamente em alguns instantes.'


def uuid_value(raw):
    try:return str(UUID(str(raw)))
    except (ValueError,TypeError,AttributeError):raise Http404('Registro não encontrado')


def timestamp(value):
    if value is None:return None
    try:return datetime.fromtimestamp(float(value),tz=timezone.utc)
    except (ValueError,OverflowError,TypeError):return None


@protected
def screen(request):
    today=django_timezone.localdate()
    context={'owners':[],'accounts':[],'connections':[],'movements':[],
        'owner_form':OwnerForm(),'sandbox_only':None,'owner_id':'','account_id':'','total':0,
        'date_from':request.GET.get('date_from') or (today-timedelta(days=30)).isoformat(),
        'date_to':request.GET.get('date_to') or today.isoformat(),'secure':request.is_secure()}
    if request.method=='POST':
        form=OwnerForm(request.POST);context['owner_form']=form
        if not form.is_valid():return render(request,'openfinance.html',context)
        try:
            result=api_post(BASE+'/owners',form.cleaned_data)
            return redirect(reverse('openfinance')+'?'+urlencode({'owner':result['id']}))
        except ApiError as exc:
            context['error']=error_message(exc)
            return render(request,'openfinance.html',context)
    if request.method!='GET':return JsonResponse({'error':'Método não permitido'},status=405)
    try:
        owners=api_get(BASE+'/owners');context['owners']=owners
        owner_id=request.GET.get('owner')
        if owner_id:
            owner_id=uuid_value(owner_id)
            selected=next((o for o in owners if o['id']==owner_id),None)
            if selected is None:raise Http404('Titular não encontrado')
        else:
            selected=owners[0] if owners else None
            owner_id=selected['id'] if selected else ''
        context.update(owner_id=owner_id,selected_owner=selected)
        health=api_get(BASE+'/health');context['sandbox_only']=health.get('sandbox_only') is True
        if selected:
            data=api_get(f'{BASE}/owners/{owner_id}/accounts')
            accounts=data.get('accounts',[]);connections=data.get('connections',[])
            for c in connections:c['last_sync_display']=timestamp(c.get('last_sync'))
            context.update(accounts=accounts,connections=connections)
            account_id=request.GET.get('account')
            if account_id:
                account_id=uuid_value(account_id)
                account=next((a for a in accounts if a['id']==account_id),None)
                if account is None:raise Http404('Conta não encontrada')
            else:
                account=accounts[0] if accounts else None
                account_id=account['id'] if account else ''
            context.update(account_id=account_id,selected_account=account)
            context['selected_connection']=next((c for c in connections if account and c['id']==account['connection_id']),None)
            period=PeriodForm({'date_from':context['date_from'],'date_to':context['date_to']})
            if not period.is_valid():context['period_error']='Confira o período informado: use datas válidas e início anterior ao fim.'
            elif account:
                try:page=max(1,min(int(request.GET.get('page','1')),100000))
                except ValueError:page=1
                params={**{k:v.isoformat() for k,v in period.cleaned_data.items()},'page':page,'page_size':100}
                statement=api_get(f'{BASE}/accounts/{account_id}/transactions',params=params)
                context.update(movements=statement.get('results',[]),total=statement.get('total',0),page=page)
                query={'owner':owner_id,'account':account_id,**{k:v for k,v in params.items() if k not in {'page','page_size'}}}
                if page>1:context['previous_url']='?'+urlencode({**query,'page':page-1})
                if page*100<context['total']:context['next_url']='?'+urlencode({**query,'page':page+1})
    except ApiError as exc:context['error']=error_message(exc)
    return render(request,'openfinance.html',context)


@protected
@require_POST
def connect_token(request,owner_id):
    try:
        data=api_post(f'{BASE}/owners/{owner_id}/connect-token',{})
        if not request.is_secure() and data.get('sandbox_only') is not True:
            return JsonResponse({'error':'Use o endereço HTTPS para autorizar contas bancárias.'},status=400)
        # No upstream error details or unrelated payload are exposed.
        return JsonResponse({'accessToken':data['accessToken'],'sandbox_only':data.get('sandbox_only') is True,
                             'widget_config':data['widget_config']})
    except ApiError as exc:return JsonResponse({'error':error_message(exc)},status=exc.status_code if exc.status_code in {401,403,404,409} else 503)


@protected
@require_POST
def attach(request,owner_id):
    if len(request.body)>2048:return JsonResponse({'error':'Requisição inválida'},status=413)
    try:
        data=json.loads(request.body)
        item_id=str(UUID(str(data['item_id'])))
    except (ValueError,KeyError,TypeError):return JsonResponse({'error':'Conexão inválida'},status=400)
    try:
        api_post(f'{BASE}/owners/{owner_id}/connections',{'item_id':item_id})
        return JsonResponse({'queued':True},status=202)
    except ApiError as exc:return JsonResponse({'error':error_message(exc)},status=exc.status_code if exc.status_code in {401,403,404,409} else 503)
