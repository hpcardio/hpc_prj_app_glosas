"""Focused payment confirmation screens and payment-only supplier document portal."""
import json
import re
import uuid
from datetime import date
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.contrib import messages
from django.http import JsonResponse,StreamingHttpResponse
from django.shortcuts import render,redirect
from django.views.decorators.http import require_http_methods
from .services import api_get,api_post,api_post_files,api_get_stream,ApiError
from .supplier_updates import error_message,display_date

BASE='/app_glosas/financeiro/contas-a-pagar/fornecedores-atualizacao/pagamentos'
PUBLIC='/app_glosas/financeiro/contas-a-pagar/fornecedores-atualizacao/publico/pagamentos'


@require_http_methods(['GET','POST'])
def listing(request):
    error=None
    if request.method=='POST':
        try:
            choices=[v.split('|',2) for v in request.POST.getlist('selected')]
            if not choices or len(choices)>100 or any(len(v)!=3 for v in choices):raise ValueError('Selecione de 1 a 100 pagamentos.')
            if len({(v[0],v[1]) for v in choices})!=1:raise ValueError('Selecione pagamentos de uma mesma empresa e fornecedor.')
            payload={'company':int(choices[0][0]),'supplier':choices[0][1],'keys':[v[2] for v in choices]}
            preview=api_post(BASE+'/preparar',payload)
            request.session['supplier_payment_selection']={**payload,'preview':preview}
            return redirect('supplier_payment_confirm')
        except (ApiError,ValueError) as exc:error=error_message(exc) if isinstance(exc,ApiError) else str(exc)
    tab='avisos' if request.GET.get('tab')=='avisos' else 'pagamentos'
    filters={k:request.GET.get(k,'') for k in ('q','company','start','end','state','page')}
    try:data=api_get(BASE+('/confirmacoes' if tab=='avisos' else '/itens'),filters,timeout=90)
    except ApiError as exc:data,error={},error_message(exc)
    for row in data.get('items',[]):
        row['date_display']=display_date(row.get('confirmed_at') if tab=='avisos' else row.get('date'),tab=='avisos')
    data.setdefault('start',date.today().replace(day=1).isoformat());data.setdefault('end',date.today().isoformat())
    page=data.get('page',1)
    params={k:v for k,v in filters.items() if k!='page' and v};params['tab']=tab
    return render(request,'supplier_payments.html',dict(data=data,filters=filters,tab=tab,error=error,
        bulk_request_key=str(uuid.uuid4()),bulk_filters=json.dumps(filters),
        previous_query=urlencode({**params,'page':max(1,page-1)}),next_query=urlencode({**params,'page':page+1})))


@require_http_methods(['GET','POST'])
def confirm(request):
    selection=request.session.get('supplier_payment_selection')
    if not selection:return redirect('supplier_payments')
    error=None
    if request.method=='POST':
        try:
            if request.POST.get('fingerprint')!=selection['preview']['fingerprint']:
                raise ValueError('A seleção foi alterada em outra aba. Confira novamente os pagamentos desta tela.')
            files=[('files',(f.name,f,f.content_type)) for f in request.FILES.getlist('files')]
            if not files:raise ValueError('Anexe ao menos um comprovante bancário.')
            payload={k:selection[k] for k in ('company','supplier','keys')}
            payload.update(fingerprint=request.POST.get('fingerprint'),confirmed=request.POST.get('confirmed')=='on',note=request.POST.get('note',''))
            result=api_post_files(BASE+'/confirmar',files,{'payload':json.dumps(payload)})
            request.session['supplier_payment_notice_'+result['id']]=result
            request.session.pop('supplier_payment_selection',None)
            messages.success(request,'Pagamento confirmado. Prepare o envio do aviso e dos comprovantes ao fornecedor.')
            return redirect('supplier_payment_notice',id=result['id'])
        except (ApiError,ValueError) as exc:error=error_message(exc) if isinstance(exc,ApiError) else str(exc)
    for item in selection['preview']['items']:item['date_display']=display_date(item.get('date'))
    return render(request,'supplier_payment_confirm.html',dict(preview=selection['preview'],error=error,note=request.POST.get('note','')))


@require_http_methods(['GET','POST'])
def notice(request,id):
    path=BASE+'/confirmacoes/'+id
    session_key='supplier_payment_notice_'+id
    error=None
    if request.method=='POST':
        action=request.POST.get('action')
        try:
            if action=='link':request.session[session_key]=api_post(path+'/link',{})
            elif action=='revoke':
                api_post(path+'/revogar',{})
                request.session.pop(session_key,None)
                messages.success(request,'Acesso aos documentos por link revogado. O histórico permanece preservado.')
            elif action=='email':
                invitation=request.session.get(session_key)
                if not invitation:raise ValueError('Prepare o link individual antes de enviar o aviso.')
                api_post(path+'/email',{'token':invitation['token'],'destination':request.POST.get('destination','')})
                messages.success(request,'E-mail com aviso e comprovantes aceito pelo servidor. Entrega e leitura não confirmadas.')
            elif action in ('prepare_whatsapp','confirm_whatsapp'):
                if not request.session.get(session_key):raise ValueError('Prepare o link individual antes de comunicar.')
                api_post(path+'/comunicacoes',dict(token=request.session[session_key]['token'],channel='whatsapp',result='preparado' if action=='prepare_whatsapp' else 'enviado_confirmado',
                    destination=request.POST.get('destination','')))
                messages.success(request,'Mensagem preparada no WhatsApp. Confirme o envio após realizá-lo.' if action=='prepare_whatsapp' else 'Envio no WhatsApp confirmado pelo operador.')
            else:raise ValueError('Ação inválida.')
            return redirect('supplier_payment_notice',id=id)
        except (ApiError,ValueError) as exc:error=error_message(exc) if isinstance(exc,ApiError) else str(exc)
    try:data=api_get(path)
    except ApiError as exc:return render(request,'supplier_payment_notice.html',{'error':error_message(exc)},status=503)
    data['confirmation']['confirmed_display']=display_date(data['confirmation'].get('confirmed_at'),True)
    for item in data['confirmation']['items']:item['date_display']=display_date(item.get('date'))
    for event in data.get('events',[]):
        event['display_at']=display_date(event.get('created_at'),True)
        event['display_kind']={'pagamento_confirmado':'Pagamento confirmado pelo financeiro','link_gerado':'Link individual preparado',
            'links_revogados':'Acesso por link revogado','email_preparado':'Envio de e-mail iniciado','email_falhou':'Falha no e-mail',
            'email_aceito_smtp':'E-mail aceito pelo servidor de envio','whatsapp_preparado':'Mensagem preparada no WhatsApp',
            'whatsapp_enviado_confirmado':'Envio no WhatsApp confirmado pelo operador'}.get(event['kind'],event['kind'])
    invitation=request.session.get(session_key)
    if invitation and invitation.get('link_id') not in data.get('active_link_ids',[]):
        invitation=None;request.session.pop(session_key,None)
    if invitation:
        phone=re.sub(r'\D','',data.get('contact',{}).get('whatsapp',''))
        if len(phone) in (10,11):phone='55'+phone
        invitation={**invitation,'whatsapp_url':'https://wa.me/'+phone+'?'+urlencode({'text':invitation['message']})}
    return render(request,'supplier_payment_notice.html',{**data,'invitation':invitation,'error':error})


def stream_response(upstream):
    def body():
        try:yield from upstream.iter_content(65536)
        finally:upstream.close()
    response=StreamingHttpResponse(body(),content_type='application/octet-stream')
    response['Content-Disposition']=upstream.headers.get('Content-Disposition','attachment')
    response['Cache-Control']='no-store, private';response['X-Content-Type-Options']='nosniff'
    return response


@require_http_methods(['GET'])
def document(request,id,file):
    try:return stream_response(api_get_stream(BASE+'/confirmacoes/'+id+'/arquivos/'+file))
    except ApiError as exc:return render(request,'supplier_payment_notice.html',{'error':error_message(exc)},status=404)


def public_payment_request(path,payload,stream=False):
    try:
        response=requests.post(settings.API_BASE_URL+PUBLIC+path,json=payload,timeout=45,stream=stream)
        if response.status_code>=400:
            message=response.text;code=response.status_code;response.close();raise ApiError(message,code)
        return response if stream else response.json()
    except (requests.RequestException,ValueError) as exc:raise ApiError('Serviço indisponível.',503) from exc


@require_http_methods(['GET','POST'])
def portal(request):
    if request.method=='POST':
        token=request.POST.get('token','')
        if not re.fullmatch(r'[A-Za-z0-9_-]{40,64}',token):return JsonResponse({'error':'Link indisponível. Solicite um novo aviso ao financeiro.'},status=410)
        try:
            if request.POST.get('action')=='download':
                response=stream_response(public_payment_request('/arquivo',{'token':token,'file':request.POST.get('file','')},stream=True))
            else:response=JsonResponse(public_payment_request('/consultar',{'token':token}))
        except ApiError as exc:response=JsonResponse({'error':error_message(exc)},status=exc.status_code if exc.status_code in (404,410,422) else 503)
    else:response=render(request,'supplier_payment_portal.html')
    response['Cache-Control']='no-store, private';response['Referrer-Policy']='same-origin';response['X-Robots-Tag']='noindex, nofollow'
    return response
