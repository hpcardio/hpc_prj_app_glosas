"""Explicit batch preview and approval; opening or refreshing never dispatches."""
import json
import re
import uuid
from django.contrib import messages
from django.shortcuts import render,redirect
from django.views.decorators.http import require_http_methods
from .services import api_get,api_post,ApiError
from .supplier_updates import display_date,error_message

BASE='/app_glosas/financeiro/contas-a-pagar/fornecedores-atualizacao/lotes'
STATUS={'ready':'Apto','blocked':'Pendente','queued':'Na fila','preparing':'Conferindo','sending':'Enviando',
    'accepted':'Aceito pelo canal','failed':'Falhou','unknown':'Sem confirmação','cancelled':'Cancelado'}
JOB_STATUS={'draft':'Prévia para conferência','queued':'Aguardando processamento','running':'Em processamento',
    'completed':'Concluído','completed_with_issues':'Concluído com pendências','cancelled':'Cancelado'}


def decorate(data):
    data['status_label']=JOB_STATUS.get(data.get('status'),data.get('status'))
    data['kind_label']='Atualização de dados' if data.get('kind')=='updates' else 'Avisos de pagamento'
    data['channel_label']='E-mail' if data.get('channel')=='email' else 'WhatsApp'
    data['created_display']=display_date(data.get('created_at'),True)
    counts=data.get('counts',{})
    data['pending_count']=sum(counts.get(s,0) for s in ('blocked','failed','unknown'))
    data['queue_count']=sum(counts.get(s,0) for s in ('queued','preparing','sending'))
    for row in data.get('recipients',[]):
        row['status_label']=STATUS.get(row['status'],row['status'])
        row['accepted_display']=display_date(row.get('accepted_at'),True)
        row['previous_display']=display_date(row.get('snapshot',{}).get('previous_send'),True)
    body=data.get('configuration',{}).get('body','')
    example=next((r['snapshot'] for r in data.get('recipients',[]) if r['snapshot']),{})
    values={1:example.get('name','[fornecedor]'),2:'[link individual]' if data.get('kind')=='updates' else 'R$ '+str(example.get('total') or '[valor]'),3:'[link individual]'}
    for index,value in values.items():body=re.sub(r'{{\s*'+str(index)+r'\s*}}',lambda _:value,body)
    data['message_example']=body
    return data


@require_http_methods(['POST'])
def create(request):
    kind=request.POST.get('kind','updates')
    try:
        ids=request.POST.getlist('selected')
        all_filtered=request.POST.get('all_filtered')=='on'
        if not ids and not all_filtered:raise ValueError('Selecione os fornecedores ou os avisos confirmados que deseja comunicar.')
        filters=json.loads(request.POST.get('filters') or '{}')
        if not isinstance(filters,dict):raise ValueError('Filtros inválidos.')
        payload=dict(kind=kind,channel=request.POST.get('channel','email'),ids=ids,all_filtered=all_filtered,
            filters=filters,request_key=request.POST.get('request_key') or str(uuid.uuid4()))
        result=api_post(BASE+'/prever',payload)
        return redirect('supplier_bulk_detail',id=result['id'])
    except (ApiError,ValueError) as exc:
        return render(request,'supplier_bulk_detail.html',{'error':error_message(exc) if isinstance(exc,ApiError) else str(exc),'kind':kind},status=400)


@require_http_methods(['GET','POST'])
def detail(request,id):
    error=None
    if request.method=='POST':
        action=request.POST.get('action')
        try:
            if action=='approve':
                if request.POST.get('confirmed')!='on':raise ValueError('Confirme a revisão dos destinatários e documentos.')
                api_post(BASE+'/jobs/'+id+'/aprovar',dict(version=int(request.POST.get('version','0')),confirmed=True,
                    confirm_resend=request.POST.get('confirm_resend')=='on',acknowledge_blocked=request.POST.get('acknowledge_blocked')=='on'))
                messages.success(request,'Lote autorizado. A fila processará os destinatários aptos; fechar a tela não cancela o lote.')
            elif action=='contact':
                api_post(BASE+'/jobs/'+id+'/destinatarios/'+request.POST.get('recipient_id',''),{'destination':request.POST.get('destination','')})
            elif action in ('refresh','cancel','retry'):
                endpoint={'refresh':'atualizar','cancel':'cancelar','retry':'repetir-falhas'}[action]
                api_post(BASE+'/jobs/'+id+'/acao/'+endpoint,{})
                messages.success(request,{'refresh':'Prévia atualizada. Confira novamente antes de autorizar.',
                    'cancel':'Cancelamento registrado para os envios pendentes.',
                    'retry':'Somente as falhas explícitas foram recolocadas na fila.'}[action])
            else:raise ValueError('Ação inválida.')
            return redirect('supplier_bulk_detail',id=id)
        except (ApiError,ValueError) as exc:error=error_message(exc) if isinstance(exc,ApiError) else str(exc)
    try:data=decorate(api_get(BASE+'/jobs/'+id,{'page':request.GET.get('page','1')}))
    except ApiError as exc:return render(request,'supplier_bulk_detail.html',{'error':error_message(exc)},status=503)
    return render(request,'supplier_bulk_detail.html',{'data':data,'error':error,'kind':data['kind']})


@require_http_methods(['GET'])
def history(request):
    kind='payments' if request.GET.get('kind')=='payments' else 'updates'
    try:
        data=api_get(BASE+'/historico',{'kind':kind,'page':request.GET.get('page','1')})
        data['items']=[decorate(item) for item in data.get('items',[])]
        error=None
    except ApiError as exc:data,error={},error_message(exc)
    return render(request,'supplier_bulk_history.html',{'data':data,'kind':kind,'error':error})
