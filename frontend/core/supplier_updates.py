"""Native supplier reconciliation screens; public routes never receive staff tokens."""
import json
import re
import hashlib
import uuid
from datetime import date,datetime
from zoneinfo import ZoneInfo
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.contrib import messages
from django.http import JsonResponse, StreamingHttpResponse
from django.core.cache import cache
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from .services import ApiError, api_get, api_post, api_get_stream

BASE = '/app_glosas/financeiro/contas-a-pagar/fornecedores-atualizacao'
PUBLIC = BASE + '/publico'
STAGES = [('a_comunicar', 'A comunicar'), ('aguardando', 'Aguardando resposta'), ('respondido', 'Respondido'),
          ('em_conferencia', 'Em conferência'), ('divergencia', 'Divergência'), ('aguardando_correcao', 'Aguardando correção'), ('validado', 'Validado')]


def display_date(value,time=False):
    if not value:return '—'
    try:
        parsed=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        if time and not parsed.tzinfo:parsed=parsed.replace(tzinfo=ZoneInfo('UTC'))
        if parsed.tzinfo:parsed=parsed.astimezone(ZoneInfo('America/Sao_Paulo'))
        return parsed.strftime('%d/%m/%Y %H:%M' if time else '%d/%m/%Y')
    except ValueError:return str(value)


def error_message(exc):
    try:
        payload = json.loads(str(exc))
        detail = payload.get('detail')
        return detail if isinstance(detail, str) else 'Confira os campos obrigatórios e os formatos informados.'
    except (ValueError, AttributeError):
        return 'Não foi possível concluir a operação. Tente novamente ou solicite apoio ao financeiro.'


def public_request(path, *, data=None, files=None):
    try:
        kwargs = {'json': data} if files is None else {'data': data, 'files': files}
        headers = {'X-Supplier-Invite': data['token']} if path == '/respostas' else {}
        response = requests.post(settings.API_BASE_URL + PUBLIC + path, timeout=45, headers=headers, **kwargs)
        if response.status_code >= 400:
            raise ApiError(response.text, response.status_code)
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        raise ApiError('Serviço temporariamente indisponível.', 503) from exc


@require_http_methods(['GET'])
def listing(request):
    filters={key:request.GET.get(key,'') for key in ('q','company','channel','stage','scope','start','end','page')}
    error=None
    try:
        data=api_get(BASE+'/comunicacoes',filters)
    except ApiError as exc:
        data,error={},error_message(exc)
    for row in data.get('items',[]):
        row['sent_display']=display_date(row.get('last_sent_at'),True)
        row['attempt_display']=display_date(row.get('last_attempt_at'),True)
        row['channel_display']={'email':'E-mail','whatsapp':'WhatsApp','telefone':'Telefone'}.get(row.get('last_channel'),'—')
        row['response_pending']=row.get('stage') in ('a_comunicar','aguardando','aguardando_correcao')
    page=data.get('page',1)
    params={k:v for k,v in filters.items() if k!='page' and v}
    return render(request,'supplier_communications.html',dict(data=data,filters=filters,error=error,stages=STAGES,
        bulk_request_key=str(uuid.uuid4()),bulk_filters=json.dumps(filters),
        previous_query=urlencode({**params,'page':max(1,page-1)}),next_query=urlencode({**params,'page':page+1})))


@require_http_methods(['GET','POST'])
def catalog(request):
    error = None
    if request.method == 'POST':
        selections = request.POST.getlist('selected')
        if len(selections) > 50:
            error = 'Selecione no máximo 50 fornecedores de cada vez.'
        else:
            created = []
            try:
                for selected in selections:
                    company, supplier = selected.split('|', 1)
                    created.append(api_post(BASE, {'company': int(company), 'supplier': supplier}))
                if len(created) == 1:
                    return redirect('supplier_update_message', case_id=created[0]['id'])
                if created:
                    messages.success(request, f'{len(created)} fornecedores incluídos no acompanhamento. Nenhuma mensagem foi enviada.')
                    return redirect(reverse('supplier_updates')+'?scope=todas')
                error = 'Selecione pelo menos um fornecedor.'
            except (ApiError, ValueError) as exc:
                error = error_message(exc)
    filters = {key: request.GET.get(key, '') for key in ('company', 'q', 'stage', 'payment', 'responsible', 'start', 'end', 'page')}
    try:
        data = api_get(BASE, filters, timeout=90)
    except ApiError as exc:
        data, error = {}, error_message(exc)
    data.setdefault('start', date(date.today().year, 1, 1).isoformat())
    data.setdefault('end', date.today().isoformat())
    data.setdefault('counts', {key: 0 for key, _ in STAGES})
    for key, _ in STAGES:
        data['counts'].setdefault(key, 0)
    params = {k: v for k, v in filters.items() if k != 'page' and v}
    page = data.get('page', 1)
    return render(request, 'supplier_updates.html', dict(data=data, filters=filters, error=error, stages=STAGES,
        previous_query=urlencode({**params, 'page': max(1, page-1)}), next_query=urlencode({**params, 'page': page+1})))


@require_http_methods(['GET', 'POST'])
def detail(request, case_id, screen='response'):
    path = f'{BASE}/{case_id}'
    session_key = f'supplier_invite_{case_id}'
    error = None
    if request.method == 'POST':
        action = request.POST.get('action')
        try:
            if action == 'invite':
                request.session[session_key] = api_post(path + '/convites', {})
                messages.success(request, 'Link individual gerado. Os links anteriores sem resposta foram revogados.')
            elif action == 'revoke':
                api_post(path + '/revogar', {})
                request.session.pop(session_key, None)
                messages.success(request, 'Convite revogado.')
            elif action == 'save_contact':
                fields = {k: request.POST.get(k, '').strip() for k in ('contact', 'email', 'phone', 'whatsapp', 'responsible', 'next_action')}
                fields.update(next_date=request.POST.get('next_date') or None, version=int(request.POST['version']))
                api_post(path + '/cadastro', fields)
                messages.success(request, 'Cadastro e próxima ação salvos.')
            elif action == 'send_email':
                invitation = request.session.get(session_key)
                if not invitation:
                    raise ValueError('Gere o link individual antes de enviar.')
                api_post(path + '/email', {'token': invitation['token'], 'destination': request.POST.get('destination', '')})
                messages.success(request, 'E-mail aceito pelo servidor de envio. Entrega e leitura ainda não confirmadas.')
            elif action in ('prepare_whatsapp', 'confirm_contact'):
                api_post(path + '/comunicacoes', dict(channel=request.POST.get('channel', 'whatsapp'),
                    result='preparado' if action == 'prepare_whatsapp' else 'enviado_confirmado',
                    destination=request.POST.get('destination', ''), notes=request.POST.get('notes', '')))
                messages.success(request, 'Mensagem preparada. Confirme o envio depois de realizá-lo no WhatsApp.' if action == 'prepare_whatsapp' else 'Comunicação registrada com confirmação do operador.')
            elif action == 'review':
                api_post(path + '/revisoes', dict(decision=request.POST.get('decision'), balance=request.POST.get('balance') or None,
                    reason=request.POST.get('reason', ''), version=int(request.POST['version']), fingerprint=request.POST.get('fingerprint', ''),
                    confirm=request.POST.get('confirm') == 'on'))
                messages.success(request, 'Conferência registrada. Esta ação não altera títulos no MV.')
            else:
                raise ValueError('Ação inválida.')
            target=reverse('supplier_update_message' if screen=='message' else 'supplier_update_detail',kwargs={'case_id':case_id})
            return redirect(target+('?tab=conferencia' if action=='review' else ''))
        except (ApiError, ValueError, KeyError) as exc:
            error = error_message(exc) if isinstance(exc, ApiError) else str(exc)
    try:
        suffix='/composicao' if screen=='message' else ('' if request.GET.get('tab')=='conferencia' else '/resposta')
        data = api_get(path+suffix, timeout=90)
    except ApiError as exc:
        return render(request, 'supplier_response.html', {'error': error_message(exc)}, status=exc.status_code if exc.status_code in (403,404) else 503)
    invitation = request.session.get(session_key)
    if invitation and invitation.get('id') not in data.get('active_invite_ids', []):
        request.session.pop(session_key, None)
        invitation = None
    if invitation:
        phone = re.sub(r'\D', '', data['case'].get('contact', {}).get('whatsapp', ''))
        if len(phone) in (10,11):
            phone = '55' + phone
        invitation = {**invitation, 'whatsapp_url': 'https://wa.me/' + phone + '?' + urlencode({'text': invitation['message']})}
    if error and request.POST.get('action') == 'save_contact':
        data['case']['contact'] = {k: request.POST.get(k, '') for k in ('contact', 'email', 'phone', 'whatsapp')}
        data['case'].update({k: request.POST.get(k, '') for k in ('responsible', 'next_action', 'next_date')})
    data.update(invitation=invitation, error=error, latest=(data.get('responses') or [None])[0], case_id=case_id, stages=STAGES)
    data['tab']='conferencia' if request.GET.get('tab')=='conferencia' else 'resposta'
    return render(request,'supplier_message.html' if screen=='message' else 'supplier_response.html',data)


@require_http_methods(['GET','POST'])
def compose(request,case_id):
    return detail(request,case_id,screen='message')


@require_http_methods(['GET'])
def attachment(request, case_id, attachment_id):
    upstream = api_get_stream(f'{BASE}/{case_id}/anexos/{attachment_id}')
    def chunks():
        try:
            yield from upstream.iter_content(65536)
        finally:
            upstream.close()
    response = StreamingHttpResponse(chunks(), content_type='application/octet-stream')
    response['Content-Disposition'] = upstream.headers.get('Content-Disposition', 'attachment')
    response['Cache-Control'] = 'no-store, private'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@require_http_methods(['GET', 'POST'])
def portal(request):
    token = request.POST.get('token', '') if request.method == 'POST' else ''
    context, status = {'token': token, 'payload': {}}, 200
    try:
        if request.method == 'POST':
            if not re.fullmatch(r'[A-Za-z0-9_-]{40,64}', token):
                raise ApiError(json.dumps({'detail': 'Convite indisponível. Solicite um novo link ao financeiro.'}), 410)
            rate_key = 'supplier-public:' + hashlib.sha256((request.META.get('REMOTE_ADDR', '') + token).encode()).hexdigest()
            if cache.add(rate_key, 1, 60):
                attempts = 1
            else:
                try:
                    attempts = cache.incr(rate_key)
                except ValueError:
                    attempts = 1
                    cache.set(rate_key, 1, 60)
            if attempts > 60:
                raise ApiError(json.dumps({'detail': 'Muitas tentativas. Aguarde um minuto antes de continuar.'}), 429)
            if request.POST.get('action') == 'consult':
                info = public_request('/consultar', data={'token': token})
                response = JsonResponse(info)
                response['Cache-Control'] = 'no-store, private'
                response['Referrer-Policy'] = 'same-origin'
                return response
            if int(request.META.get('CONTENT_LENGTH') or 0) > 26*1024*1024:
                raise ApiError(json.dumps({'detail': 'Envie no máximo 25 MB de anexos.'}), 413)
            raw = request.POST.get('payload', '{}')
            context['payload'] = json.loads(raw)
            files = [('files', (file.name, file, file.content_type)) for file in request.FILES.getlist('files')]
            if len(files) > 10:
                raise ApiError(json.dumps({'detail': 'Envie no máximo 10 arquivos.'}), 422)
            context['receipt'] = public_request('/respostas', data={'token': token, 'payload': raw}, files=files)
    except (ApiError, ValueError) as exc:
        context['error'] = error_message(exc)
        status = getattr(exc, 'status_code', None) or 422
        if status not in (410, 413, 422, 503):
            status = 503
        context['unavailable'] = status == 410
        if request.POST.get('action') == 'consult':
            response = JsonResponse({'error': context['error']}, status=status)
            response['Cache-Control'] = 'no-store, private'
            return response
    response = render(request, 'supplier_portal.html', context, status=status)
    response['Cache-Control'] = 'no-store, private'
    response['Referrer-Policy'] = 'same-origin'
    response['X-Robots-Tag'] = 'noindex, nofollow'
    return response
