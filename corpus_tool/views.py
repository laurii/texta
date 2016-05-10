# -*- coding: utf8 -*-
import calendar
import json
import csv
import logging
import time
from datetime import datetime, timedelta as td

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, StreamingHttpResponse
from django.template import loader, Context
from django.utils.encoding import smart_str

from conceptualiser.models import Term,TermConcept
from corpus_tool.models import Search
from es_manager.es_manager import ES_Manager
from settings import STATIC_URL, URL_PREFIX, date_format, es_links, INFO_LOGGER, ERROR_LOGGER
from permission_admin.models import Dataset
from utils.datasets import get_active_dataset

ES_SCROLL_BATCH = 100

try:
    from cStringIO import StringIO
except:
    from StringIO import StringIO


def ngrams(input_list, n):
  return zip(*[input_list[i:] for i in range(n)])


def convert_date(date_string,frmt):
    return datetime.strptime(date_string,frmt).date()


def get_fields(es_m):
    """ Crete field list from fields in the Elasticsearch mapping
    """
    fields = []
    mapped_fields = es_m.get_mapped_fields()

    for data in mapped_fields:
        path = data['path']
        path_list = path.split('.')
        label = '{0} --> {1}'.format(path_list[0], ' --> '.join(path_list[1:])) if len(path_list) > 1 else path_list[0]
        label = label.replace('-->', u'→')
        field = {'data': json.dumps(data), 'label': label}
        fields.append(field)

        # Add additional field if it has fact
        if es_m.check_if_field_has_facts(path_list):
            data['type'] = 'facts'
            label += ' [facts]'
            field = {'data': json.dumps(data), 'label': label}
            fields.append(field)

    # Sort fields by label
    fields = sorted(fields, key=lambda l: l['label'])

    return fields


@login_required
def index(request):
    # Define selected mapping
    dataset, mapping, date_range = get_active_dataset(request.session['dataset'])
    es_m = ES_Manager(dataset, mapping, date_range)

    fields = get_fields(es_m)

    template = loader.get_template('corpus_tool/corpus_tool_index.html')
    return HttpResponse(template.render({'STATIC_URL':STATIC_URL,
                       'URL_PREFIX':URL_PREFIX,
                       'fields':fields,
                       'date_range':date_range,
                       'searches':Search.objects.filter(author=request.user),
                       'dataset':dataset},request))


def date_ranges(date_range,interval):
    frmt = "%Y-%m-%d"
    
    ranges = []
    labels = []

    date_min = convert_date(date_range['min'],frmt)
    date_max = convert_date(date_range['max'],frmt)
    
    if interval == 'year':
        for yr in range(date_min.year,date_max.year+1):
            ranges.append({'from':str(yr)+'-01-01','to':str(yr+1)+'-01-01'})
            labels.append(yr)
    if interval == 'quarter':
        for yr in range(date_min.year,date_max.year+1):
            for i,quarter in enumerate([(1,3),(4,6),(7,9),(10,12)]):
                end = calendar.monthrange(yr,quarter[1])[1]
                ranges.append({'from':'-'.join([str(yr),str(quarter[0]),'01']),'to':'-'.join([str(yr),str(quarter[1]),str(end)])})
                labels.append('-'.join([str(yr),str(i+1)+'Q']))
    if interval == 'month':
        for yr in range(date_min.year,date_max.year+1):
            for month in range(1,13):
                month_max = str(calendar.monthrange(yr,month)[1])
                if month < 10:
                    month = '0'+str(month)
                else:
                    month = str(month)
                ranges.append({'from':'-'.join([str(yr),month,'01']),'to':'-'.join([str(yr),month,month_max])})
                labels.append('-'.join([str(yr),month]))
    if interval == 'day':
        d1 = date_min
        d2 = date_max+td(days=1)
        delta = d2-d1
        dates = [d1+td(days=i) for i in range(delta.days+1)]
        for date_pair in ngrams(dates,2):
            ranges.append({'from':date_pair[0].strftime(frmt),'to':date_pair[1].strftime(frmt)})
            labels.append(date_pair[0].strftime(frmt))

    return ranges,labels


def zero_list(n):
    listofzeros = [0] * n
    return listofzeros


def display_encode(s):
    try:
        return s.encode('latin1')
    except Exception as e:
        print e
        #TODO: What is the intention here?  Why is latin1 first? What are the appropriate exceptions instead of catchall)
        return s.encode('utf8')


@login_required
def save(request):

    es_params = request.POST
    dataset, mapping, date_range = get_active_dataset(request.session['dataset'])
    es_m = ES_Manager(dataset, mapping, date_range)
    es_m.build(es_params)
    combined_query = es_m.get_combined_query()

    try:
        q = combined_query
        desc = request.POST['search_description']
        search = Search(author=request.user,description=desc,dataset=Dataset.objects.get(pk=int(request.session['dataset'])),query=json.dumps(q))
        search.save()
        logging.getLogger(INFO_LOGGER).info(json.dumps({'process':'SAVE SEARCH','event':'search_saved','args':{'user_name':request.user.username,'desc':desc},'data':{'search_id':search.id}}))
    except Exception as e:
        print 'Exception', e
        logging.getLogger(ERROR_LOGGER).error(json.dumps({'process':'SAVE SEARCH','event':'search_saving_failed','args':{'user_name':request.user.username}}),exc_info=True)
    return HttpResponse()


@login_required
def delete(request):
    try:
        Search.objects.get(pk=request.GET['pk']).delete()
        logging.getLogger(INFO_LOGGER).info(json.dumps({'process':'DELETE SEARCH','event':'search_deleted','args':{'user_name':request.user.username,'search_id':int(request.GET['pk'])}}))
    except Exception as e:
        print e
        logging.getLogger(ERROR_LOGGER).error(json.dumps({'process':'DELETE SEARCH','event':'search_deletion_failed','args':{'user_name':request.user.username,'search_id':int(request.GET['pk'])}}),exc_info=True)
    return HttpResponse(request.GET['pk'])


def autocomplete(request):

    field_name = request.POST['field_name']
    field_id = request.POST['id']
    content = request.POST['content']
    autocomplete_data = request.session['autocomplete_data']

    suggestions = []

    if field_name in autocomplete_data.keys():
        for term in autocomplete_data[field_name]:
            suggestions.append("<li class=\"list-group-item\" onclick=\"insert('','"+str(field_id)+"','"+smart_str(term)+"');\">"+smart_str(term)+"</li>")
    else:

        last_line = content.split('\n')[-1] if content else ''

        if len(last_line) > 0:
            terms = Term.objects.filter(term__startswith=last_line).filter(author=request.user)
            seen = {}
            suggestions = []
            for term in terms[:10]:
                for term_concept in TermConcept.objects.filter(term=term.pk):
                    concept = term_concept.concept
                    concept_term = (concept.pk,term.term)
                    if concept_term not in seen:
                        seen[concept_term] = True
                        display_term = term.term.replace(last_line,'<font color="red">'+last_line+'</font>')
                        display_text = "<b>"+smart_str(display_term)+"</b> @"+smart_str(concept.pk)+"-"+smart_str(concept.descriptive_term.term)
                        suggestions.append("<li class=\"list-group-item\" onclick=\"insert('"+str(concept.pk)+"','"+str(field_id)+"','"+smart_str(concept.descriptive_term.term)+"');\">"+display_text+"</li>")

    return HttpResponse(suggestions)


@login_required
def get_saved_searches(request):  
    searches = Search.objects.filter(author=request.user).filter(dataset=Dataset(pk=int(request.session['dataset'])))
    return HttpResponse(json.dumps([{'id':search.pk,'desc':search.description} for search in searches],ensure_ascii=False))


@login_required
def get_examples_table(request):
    # Define selected mapping
    dataset, mapping, date_range = get_active_dataset(request.session['dataset'])
    es_m = ES_Manager(dataset, mapping, date_range)

    # get columns names from ES mapping
    fields = es_m.get_column_names()

    template = loader.get_template('corpus_tool/corpus_tool_results.html')
    return HttpResponse(template.render({'STATIC_URL':STATIC_URL,
                       'URL_PREFIX':URL_PREFIX,
                       'fields':fields,
                       'date_range':date_range,
                       'searches':Search.objects.filter(author=request.user),
                       'columns':[{'index':index, 'name':field_name} for index, field_name in enumerate(fields)],
                       'dataset':dataset,
                       'mapping':mapping}),request)


@login_required
def get_examples(request):
    filter_params = json.loads(request.GET['filterParams'])
    es_params = {filter_param['name']: filter_param['value'] for filter_param in filter_params}
    es_params['examples_start'] = request.GET['iDisplayStart']
    es_params['num_examples'] = request.GET['iDisplayLength']
    result = search(es_params, request)

    return HttpResponse(json.dumps(result, ensure_ascii=False))


def merge_spans(spans):
    """ Merge spans range
    """
    merged_spans = []
    sort_spans = sorted(spans, key=lambda l:l[0])
    total_spans = len(sort_spans)
    if total_spans:
        merged_spans.append(sort_spans[0])
        i = 1
        while i < total_spans:
            m_last = list(merged_spans[-1])
            s_next = list(sort_spans[i])
            if s_next[0] > m_last[1]:
                merged_spans.append(s_next)
            if (s_next[0] <= m_last[1]) and (s_next[1] > m_last[1]):
                merged_spans[-1] = [m_last[0], s_next[1]]
            i += 1
    return merged_spans


def search(es_params, request):

    try:

        start = time.time()
        out = {'column_names': [],
               'aaData': [],
               'iTotalRecords': 0,
               'iTotalDisplayRecords': 0,
               'lag': 0}

        # Define selected mapping
        dataset, mapping, date_range = get_active_dataset(request.session['dataset'])
        es_m = ES_Manager(dataset, mapping, date_range)

        es_m.build(es_params)

        ### DEFINING THE EXAMPLE SIZE
        es_m.set_query_parameter('from', es_params['examples_start'])
        es_m.set_query_parameter('size', es_params['num_examples'])

        ### HIGHLIGHTING THE MATCHING FIELDS
        pre_tag = "<span style='background-color:#FFD119'>"
        post_tag = "</span>"
        highlight_config = {"fields": {}, "pre_tags": [pre_tag], "post_tags": [post_tag]}
        for field in es_params:
            if 'match_field' in field and es_params['match_operator_'+field.split('_')[-1]] != 'must_not':
                f = es_params[field]
                highlight_config['fields'][f] = {"number_of_fragments": 0}
        es_m.set_query_parameter('highlight', highlight_config)

        response = es_m.search()
        facts_map = es_m.get_facts_map()
        facts_highlight = facts_map['include']

        out['iTotalRecords'] = response['hits']['total']
        out['iTotalDisplayRecords'] = response['hits']['total']

        # get columns names from ES mapping
        out['column_names'] = es_m.get_column_names()

        for hit in response['hits']['hits']:

            hit_id = hit['_id']

            row = []
            # Fill the row content respecting the order of the columns
            for col in out['column_names']:

                # If the content is nested, need to break the flat name in a path list
                filed_path = col.split('.')

                # Get content for this field path:
                #   - Starts with the hit structure
                #   - For every field in field_path, retrieve the specific content
                #   - Repeat this until arrives at the last field
                #   - If the field in the field_path is not in this hit structure,
                #     make content empty (to allow dynamic mapping without breaking alignment)
                content = hit['_source']
                for p in filed_path:
                    content = content[p] if p in content else ''

                # If has facts, highlight
                if hit_id in facts_highlight and col in facts_highlight[hit_id]:
                    fact_spans = facts_highlight[hit_id][col]
                    # Merge overlapping spans
                    fact_spans = merge_spans(fact_spans)
                    rest_sentence = content
                    corpus_facts = ''
                    last_cut = 0
                    # Apply span tagging
                    for span in fact_spans:
                        start = span[0] - last_cut
                        end = span[1] - last_cut
                        b_sentence = rest_sentence[0:start]
                        f_sencente = rest_sentence[start:end]
                        rest_sentence = rest_sentence[end:]
                        last_cut = span[1]
                        corpus_facts += b_sentence
                        corpus_facts += "<span style='background-color:#A3E4D7'>"
                        corpus_facts += f_sencente
                        corpus_facts += "</span>"
                    corpus_facts += rest_sentence
                    content = corpus_facts

                # If content in the highlight structure, replace it with the tagged hit['highlight']
                if col in highlight_config['fields'] and 'highlight' in hit:
                    content = hit['highlight'][col][0]

                # CHECK FOR EXTERNAL RESOURCES
                link_key = (dataset, mapping, col)
                if link_key in es_links:
                    link_prefix, link_suffix = es_links[link_key]
                    content = '<a href="'+str(link_prefix)+str(content)+str(link_suffix)+'" target="_blank">'+str(content)+'</a>'

                # Append the final content of this col to the row
                row.append(content)

            out['aaData'].append(row)

        out['lag'] = time.time()-start
        logging.getLogger(INFO_LOGGER).info(json.dumps({'process': 'SEARCH CORPUS',
                                                        'event': 'documents_queried',
                                                        'args': {'query': es_m.get_combined_query(),
                                                                 'user_name': request.user.username}}))
        return out

    except Exception, e:
        print "--- Exception: {0} ".format(e)
        logging.getLogger(ERROR_LOGGER).error(json.dumps({'process':'SEARCH CORPUS','event':'documents_queried','args':{'user_name':request.user.username}}),exc_info=True)
        template = loader.get_template('corpus_tool/corpus_tool_results.html')
        context = Context({'STATIC_URL': STATIC_URL,
                           'name': request.user.username,
                           'logged_in': True,
                           'data': '',
                           'error': 'query failed'})
        # TODO: review this behavior - No error msgs is shown to the user
        out = {'column_names': [], 'aaData': [], 'iTotalRecords': 0, 'iTotalDisplayRecords': 0, 'lag': 0}
        return out


@login_required
def export_pages(request):

    es_params = {entry['name']: entry['value'] for entry in json.loads(request.GET['args'])}

    if es_params['num_examples'] == '*':
        response = StreamingHttpResponse(get_all_rows(es_params, request), content_type='text/csv')
    else:
        response = StreamingHttpResponse(get_rows(es_params, request), content_type='text/csv')
    
    response['Content-Disposition'] = 'attachment; filename="%s"' % (es_params['filename'])

    return response


def get_rows(es_params, request):

    buffer_ = StringIO()
    writer = csv.writer(buffer_)
    
    writer.writerow(es_params['features'])

    dataset, mapping, date_range = get_active_dataset(request.session['dataset'])
    es_m = ES_Manager(dataset, mapping, date_range)
    es_m.build(es_params)

    es_m.set_query_parameter('from', es_params['examples_start'])
    q_size = es_params['num_examples'] if es_params['num_examples'] <= ES_SCROLL_BATCH else ES_SCROLL_BATCH
    es_m.set_query_parameter('size', q_size)

    features = {feature: None for feature in es_params['features'] }

    response = es_m.scroll()

    scroll_id = response['_scroll_id']
    left = es_params['num_examples']
    hits = response['hits']['hits']
    
    while hits and left:
        rows = []
        for hit in hits:
            row = []
            for field,content in sorted(hit['_source'].items(),key = lambda x: x[0]):
                if field in features:
                    row.append(content)
            rows.append(row)

        if left > len(rows):
            for row in rows:
                writer.writerow([element.encode('utf-8') if isinstance(element,unicode) else element for element in row])
            buffer_.seek(0)
            data = buffer_.read()
            buffer_.seek(0)
            buffer_.truncate()
            yield data
            
            left -= len(rows)
            response = es_m.scroll(scroll_id=scroll_id)
            hits = response['hits']['hits']
            scroll_id = response['_scroll_id']

        elif left == len(rows):
            for row in rows:
                writer.writerow([element.encode('utf-8') if isinstance(element,unicode) else element for element in row])
            buffer_.seek(0)
            data = buffer_.read()
            buffer_.seek(0)
            buffer_.truncate()
            yield data
            
            break
        else:
            for row in rows[:left]:
                writer.writerow([element.encode('utf-8') if isinstance(element,unicode) else element for element in row])
            buffer_.seek(0)
            data = buffer_.read()
            buffer_.seek(0)
            buffer_.truncate()
            yield data
            
            break


def get_all_rows(es_params, request):
    buffer_ = StringIO()
    writer = csv.writer(buffer_)
    
    writer.writerow(es_params['features'])
    
    dataset, mapping, date_range = get_active_dataset(request.session['dataset'])
    es_m = ES_Manager(dataset, mapping, date_range)
    es_m.build(es_params)

    es_m.set_query_parameter('size', ES_SCROLL_BATCH)

    features = {feature: None for feature in es_params['features']}

    response = es_m.scroll()

    scroll_id = response['_scroll_id']
    hits = response['hits']['hits']
    
    while hits:
        for hit in hits:
            row = []
            for field,content in sorted(hit['_source'].items(),key = lambda x: x[0]):
                if field in features:
                    row.append(content)
            writer.writerow([element.encode('utf-8') if isinstance(element,unicode) else element for element in row])
        
        buffer_.seek(0)
        data = buffer_.read()
        buffer_.seek(0)
        buffer_.truncate()
        yield data

        response = es_m.scroll(scroll_id=scroll_id)
        hits = response['hits']['hits']
        scroll_id = response['_scroll_id']


def aggregate(request):
    es_params = request.POST

    try:

        aggregation_data = es_params['aggregate_over']
        aggregation_data = json.loads(aggregation_data)
        field_type = aggregation_data['type']

        # Define selected mapping
        dataset, mapping, date_range = get_active_dataset(request.session['dataset'])

        if field_type == 'date':
            data = timeline(es_params, request)
            
            for i, str_val in enumerate(data['data'][0]):
                data['data'][0][i] = str_val.decode('unicode-escape')
        else:
            data = discrete_agg(es_params, request)
        
        logging.getLogger(INFO_LOGGER).info(json.dumps({'process':'SEARCH CORPUS','event':'aggregation_queried','args':{'user_name':request.user.username}}))

        return HttpResponse(json.dumps(data))
        
    except Exception as e:
        print e
        logging.getLogger(ERROR_LOGGER).error(json.dumps({'process':'SEARCH CORPUS','event':'aggregation_query_failed','args':{'user_name':request.user.username}}),exc_info=True)
        return HttpResponse()


def timeline(es_params, request):

    series_names = ['Date']
    data = []
    series = []
    labels = []
    
    try:
        interval = es_params['interval']

        # Define selected mapping
        dataset, mapping, date_range = get_active_dataset(request.session['dataset'])
        es_m = ES_Manager(dataset, mapping, date_range)


        aggregation_data = es_params['aggregate_over']
        aggregation_data = json.loads(aggregation_data)
        aggregation_field = aggregation_data['path']

        # temporary
        ranges,labels = date_ranges(date_range,interval)
        aggregations = {"ranges": {"date_range": {"field": aggregation_field, "format": date_format, "ranges": ranges}}}

        # find saved searches
        for item in es_params:
            if 'saved_search' in item:
                s = Search.objects.get(pk=es_params[item])
                name = s.description
                saved_query = json.loads(s.query)
                es_m.load_combined_query(saved_query)
                es_m.set_query_parameter('aggs', aggregations)
                response = es_m.search()
                normalised_counts,_ = normalise_agg(response, es_m, es_params, 'ranges')
                series.append({'name': name.encode('latin1'), 'data': normalised_counts})

        # add current search
        es_m.build(es_params)

        if not es_m.is_combined_query_empty():
            es_m.set_query_parameter('aggs', aggregations)
            response = es_m.search()
            normalised_counts,_ = normalise_agg(response, es_m, es_params, 'ranges')
            series.append({'name':'Query','data':normalised_counts})

        data.append(['Date']+labels)
        for serie in series:
            data.append([serie['name']]+serie['data'])
        data = map(list, zip(*data))

        logging.getLogger(INFO_LOGGER).info(json.dumps({'process':'SEARCH CORPUS','event':'timeline_queried','args':{'user_name':request.user.username}}))
    except Exception as e:
        logging.getLogger(ERROR_LOGGER).error(json.dumps({'process':'SEARCH CORPUS','event':'timeline_query_failed','args':{'user_name':request.user.username}}),exc_info=True)
    return {'data':data,'type':'line','height':'500','distinct_values':None}


def discrete_agg(es_params, request):
    distinct_values = []
    query_results = []
    lexicon = []

    aggregation_data = es_params['aggregate_over']
    aggregation_data = json.loads(aggregation_data)
    aggregation_field = aggregation_data['path']

    try:

        aggregations = {"strings" : {es_params['sort_by']: {"field": aggregation_field,'size': 50}},
                        "distinct_values": {"cardinality": {"field": aggregation_field}}}

        # Define selected mapping
        dataset,mapping,date_range = get_active_dataset(request.session['dataset'])
        es_m = ES_Manager(dataset, mapping, date_range)

        for item in es_params:
            if 'saved_search' in item:
                s = Search.objects.get(pk=es_params[item])
                name = s.description
                saved_query = json.loads(s.query)
                es_m.load_combined_query(saved_query)
                es_m.set_query_parameter('aggs', aggregations)
                response = es_m.search()
                normalised_counts,labels = normalise_agg(response, es_m, es_params, 'strings')
                lexicon = list(set(lexicon+labels))
                query_results.append({'name':name,'data':normalised_counts,'labels':labels})
                distinct_values.append({'name':name,'data':response['aggregations']['distinct_values']['value']})

        es_m.build(es_params)
        # FIXME
        # this is confusing for the user
        if not es_m.is_combined_query_empty():
            es_m.set_query_parameter('aggs', aggregations)
            response = es_m.search()
            normalised_counts,labels = normalise_agg(response, es_m, es_params, 'strings')
            lexicon = list(set(lexicon+labels))
            query_results.append({'name':'Query','data':normalised_counts,'labels':labels})
            distinct_values.append({'name':'Query','data':response['aggregations']['distinct_values']['value']})

        data = [a+zero_list(len(query_results)) for a in map(list, zip(*[lexicon]))]
        data = [['Word']+[query_result['name'] for query_result in query_results]]+data

        for i,word in enumerate(lexicon):          
            for j,query_result in enumerate(query_results):               
                for k,label in enumerate(query_result['labels']):
                    if word == label:
                        data[i+1][j+1] = query_result['data'][k]
        
        logging.getLogger(INFO_LOGGER).info(json.dumps({'process':'SEARCH CORPUS','event':'discrete_aggregation_queried','args':{'user_name':request.user.username}}))
    except Exception as e:
        print 'Exception', e
        logging.getLogger(ERROR_LOGGER).error(json.dumps({'process':'SEARCH CORPUS','event':'discrete_aggregation_query_failed','args':{'user_name':request.user.username}}),exc_info=True)
    return {'data':[data[0]]+sorted(data[1:], key=lambda x: sum(x[1:]), reverse=True),'height':len(data)*15,'type':'bar','distinct_values':json.dumps(distinct_values)}


def normalise_agg(response, es_m, es_params, agg_type):

    raw_counts = [bucket['doc_count'] for bucket in response['aggregations'][agg_type]['buckets']]
    bucket_labels = []
    if agg_type == 'strings':
        for a in response['aggregations']['strings']['buckets']:
            try:
                bucket_labels.append(a['key'])
            except KeyError:
                bucket_labels.append(smart_str(a['key']))

    if es_params['frequency_normalisation'] == 'relative_frequency':

        es_m.set_query_parameter('query', {"match_all": {}})
        response_all = es_m.search(apply_facts=False)

        total_counts = [bucket['doc_count'] for bucket in response_all['aggregations'][agg_type]['buckets']]
        relative_counts = [float(raw_counts[i])/total_counts[i] if total_counts[i] != 0 else 0 for i in range(len(total_counts))]
        return relative_counts,bucket_labels
    else:
        return raw_counts,bucket_labels
