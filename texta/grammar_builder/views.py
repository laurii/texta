import json

from django.http import HttpResponse, HttpResponseRedirect
from django.template import loader, Context
from django.contrib.auth.decorators import login_required
from estnltk import Regex, Lemmas, Concatenation, Union, Text
import requests

from ..corpus_tool.views import Search
from ..utils.datasets import Datasets
from ..utils.es_manager import ES_Manager

from settings import STATIC_URL, URL_PREFIX, es_url

@login_required
def index(request):
    # Define selected mapping
    ds = Datasets().activate_dataset(request.session)
    dataset = ds.get_index()
    mapping = ds.get_mapping()

    ds = Datasets().activate_dataset(request.session)
    es_m = ds.build_manager(ES_Manager)
    fields = es_m.get_column_names()

    searches = [{'id':search.pk,'desc':search.description} for search in Search.objects.filter(author=request.user)]

    template = loader.get_template('grammar_builder/grammar_builder_index.html')
    return HttpResponse(template.render({'STATIC_URL':STATIC_URL,
                                         'searches':searches,
                                         'features':fields},request))


@login_required
def get_table(request):
    """
    search_id = request.POST['searchId']
    
    search = Search.objects.get(pk=search_id)
    
    print search.query
    """
    template = loader.get_template('grammar_builder/grammar_builder_table.html')
    
    return HttpResponse(template.render({'feature':request.POST['feature']},request))


@login_required
def get_table_data(request):
    feature = request.GET['feature']
    search_id = request.GET['search_id']
    constructs = json.loads(request.GET['constructs'])
    
    print constructs
    
    query = json.loads(Search.objects.get(pk=search_id).query)
    
    query["from"] = request.GET['iDisplayStart']
    query["size"] = request.GET['iDisplayLength']
    query["fields"] = [feature]
    
    data = scroll_data(query,request)
    
    instructions = []
    
    for construct in constructs:
        name = construct['name']
        separator = construct['separator']
        raw_components = construct['components']
        
        components = []
        
        for raw_component in raw_components:
            if raw_component['type'] == 'Lemmas':
                components.append(Lemmas(*(raw_component['content'].strip().split('\n')),name=raw_component['name']))
            elif raw_component['type'] == 'Regex':
                components.append(Regex(raw_component['content'],name=raw_component['name']))
        
        instructions.append(Concatenation(*components, sep=Regex(separator)))
    
    instruction = Union(*instructions, name='instruction')
    
    for row in data['aaData']:
        if isinstance(row[0],unicode):
            text = Text(row[0])
            row.append(any([len(instruction.get_matches(sentence)) > 0 for sentence in text.split_by('sentences')]))
    
    return HttpResponse(json.dumps(data,ensure_ascii=False))


def scroll_data(query,request):   
    out = {'aaData':[],'iTotalRecords':0,'iTotalDisplayRecords':0}
    try:
        q = query

        # Define selected mapping
        ds = Datasets().activate_dataset(request.session)
        dataset = ds.get_index()
        mapping = ds.get_mapping()

        response = ES_Manager.plain_search(es_url, dataset, mapping, q)

        out['iTotalRecords'] = response['hits']['total']
        out['iTotalDisplayRecords'] = response['hits']['total']
        
        for hit in response['hits']['hits']:
            row = hit['fields'][q["fields"][0]]
            out['aaData'].append(row)
        """
        try:
            hit['_source'].keys()
        except UnboundLocalError as e:
            ### no hits, thus no column names : (
            print e
        """
        #logging.getLogger(INFO_LOGGER).info(json.dumps({'process':'SEARCH CORPUS','event':'documents_queried','args':{'user_name':request.user.username}}))
        
    except Exception as e:        
        print "Exception", e
        #logging.getLogger(ERROR_LOGGER).error(json.dumps({'process':'SEARCH CORPUS','event':'documents_queried','args':{'user_name':request.user.username}}),exc_info=True)

    return out
