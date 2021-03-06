# -*- coding: utf8 -*-

import json

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseRedirect
from django.template import loader
import requests

from ..utils.datasets import Datasets
from ..utils.es_manager import ES_Manager

from settings import STATIC_URL, URL_PREFIX, es_url

@login_required
def index(request):
    template = loader.get_template('document_miner/index.html')
    request.session['seen_docs'] = []

    # Define selected mapping
    ds = Datasets().activate_dataset(request.session)
    dataset = ds.get_index()
    mapping = ds.get_mapping()

    # Get field names and types
    ds = Datasets().activate_dataset(request.session)
    es_m = ds.build_manager(ES_Manager)
    fields = es_m.get_column_names()
    
    return HttpResponse(template.render({'STATIC_URL':STATIC_URL,'fields':fields},request))

def add_documents(ids,index,mapping):
    out = []
    for id in ids:
        out.append({"_index" : index, "_type" : mapping, "_id" : id})
    return out

@login_required
def query(request):
    out = []
    field = request.POST['field']

    # Define selected mapping
    ds = Datasets().activate_dataset(request.session)
    dataset = ds.get_index()
    mapping = ds.get_mapping()

    handle_negatives = request.POST['handle_negatives']
    ids = [a.strip() for a in request.POST['docs'].split('\n')]
    docs_declined = json.loads(request.POST['docs_declined'])

    stopwords = [a.strip() for a in request.POST['stopwords'].split('\n')]

    mlt = {
        "more_like_this": {
            "fields" : [field],
            "like" : add_documents(ids,dataset,mapping),
            "min_term_freq" : 1,
            "max_query_terms" : 12,
        }
    }

    if stopwords:
        mlt["more_like_this"]["stop_words"] = stopwords

    query = {
        "query":{
            "bool":{
                "must":[mlt]
            }
        },
        "size":20,
        "highlight" : {
            "pre_tags" : ["<b>"],
            "post_tags" : ["</b>"],
            "fields" : {
                field : {}
            }
        }
    }

    if handle_negatives == 'unlike':
        mlt["more_like_this"]["unlike"] = add_documents(docs_declined,dataset,mapping)
    else:
        if docs_declined:
            declined = [{'ids':{'values':docs_declined}}]
            query["query"]["bool"]["must_not"] = declined

    print query

    response = ES_Manager.plain_search(es_url, dataset, mapping, query)
    
    try:
        for hit in response['hits']['hits']:
            try:
                row = '''
                    <tr id="row_'''+hit['_id']+'''">
                        <td><a not_yet_accepted="'''+hit['_id']+'''" href="javascript:accept_document('''+hit['_id']+''')">Accept</a></td>
                        <td>'''+hit['_id']+'''</td>
                        <td>'''+'\n'.join(hit['highlight'][field])+'''</td>
                    </tr>
                '''
            except KeyError:
                row = '''
                    <tr id="row_'''+hit['_id']+'''">
                        <td><a href="javascript:accept_document('''+hit['_id']+''')">Accept</a></td>
                        <td><a href="javascript:decline_document('''+hit['_id']+''')">Reject</a></td>
                        <td>'''+hit['_id']+'''</td>
                        <td>'''+hit['_source'][field]+'''</td>
                    </tr>
                '''            
            out.append(row)
    except:
        KeyError

    if not out:
        out.append('No matches from supported IDs.')

    return HttpResponse('<table class="table">'+''.join(out)+'</table>')

