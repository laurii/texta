import requests
from sympy import symbols, simplify
from sympy.core.symbol import Symbol
import operator
from functools import reduce

import requests

#from ..utils.es_manager import ES_Manager

class ElasticGrammarQuery(object):
    
    def __init__(self, inclusive_grammar, exclusive_grammar, elastic_url, index, mapping):
        if not self._are_names_unique(inclusive_grammar):
            raise Exception()
        #if not self._are_names_unique(exclusive_grammar):
            #raise Exception()
        
        self._query = self._construct_elastic_query(inclusive_grammar, exclusive_grammar)
        #{'op':'concatenate', 'components':[{'op':'match','terms':['tere','ilus'],'layer':'text','name':'asd'}],'name':'asd'}
        print(self._query)
    
    def _construct_elastic_query(self, inclusive_grammar, exclusive_grammar):
        terminal_name_to_component = self._extract_terminal_components(inclusive_grammar)
        logical_expression = self._build_logical_expression(inclusive_grammar, [name for name in terminal_name_to_component]) # Build a simplistic logical expression for Elastic Search to match at least all the potential documents
        print(logical_expression)
        query = {'main':{'query':{'bool':{'should':[],'must':[],'must_not':[]}}}}

        self._add_constraints(query, logical_expression, terminal_name_to_component)

        return query

    def _add_constraints(self, query, logical_expression, terminal_name_to_component, branch='should'):
                
        def add_constraints(expression, query_node):
            if expression.__class__.__name__ == 'And':
                query_node.append({'and':[]})
                current_node = query_node[-1]['and']
                for sub_expression in expression.args:
                    add_constraints(sub_expression, current_node)
            elif expression.__class__.__name__ == 'Or':
                query_node.append({'or':[]})
                current_node = query_node[-1]['or']
                for sub_expression in expression.args:
                    add_constraints(sub_expression, current_node)
            elif expression.__class__.__name__ == 'Symbol':
                terminal = expression
                terminal_data = terminal_name_to_component[terminal.name]
                current_node = query_node
                
                operation = terminal_data['op']
                if operation == 'match':
                    if len(terminal_data['terms']) > 1:
                        joining_operator = 'or' if terminal_data['join_by'] == 'union' else 'and'
                        current_node.append({joining_operator: []})
                        current_node = current_node[-1][joining_operator]
                    for term in terminal_data['terms']:
                        current_node.append({"match": {terminal_data['layer']:term}})
                elif operation == 'regex':
                    current_node.append({'regexp': {terminal_data['layer']:terminal_data['expression']}})

        add_constraints(logical_expression, query['main']['query']['bool'][branch])
        
    def _are_names_unique(self, grammar):
        nodes = [grammar]
        names = set()
        while len(nodes) > 0:
            current_node = nodes.pop()
            current_name = current_node['name']
            if current_name in names:
                return False
            names.add(current_name)
            if 'components' in current_node:
                nodes.extend(current_node['components'])
        return True
    
    def _build_logical_expression(self, grammar, terminal_component_names):
        terminal_component_symbols = eval("symbols('%s')"%(' '.join(terminal_component_names)))
        if isinstance(terminal_component_symbols, Symbol):
            terminal_component_symbols = [terminal_component_symbols]
        name_to_symbol = {terminal_component_names[i]:symbol for i, symbol in enumerate(terminal_component_symbols)}
        terminal_component_names = set(terminal_component_names)
        
        op_to_symbolic_operation = {'not':operator.invert, 'concat':operator.and_, 'gap':operator.and_, 'union':operator.or_, 'intersect':operator.and_}
        
        def logical_expression_builder(component):
            if component['name'] in terminal_component_names:
                return name_to_symbol[component['name']]
            else:
                children = component['components']
                return reduce(op_to_symbolic_operation[component['op']],[logical_expression_builder(child) for child in children])
        
        return simplify(logical_expression_builder(grammar))
    
    def _extract_terminal_components(self, grammar):
        nodes = [grammar]
        names = {}
        while len(nodes) > 0:
            current_node = nodes.pop()
            current_name = current_node['name']
            if 'layer' in current_node:
                names[current_name] = current_node
            else:
                nodes.extend(current_node['components'])
        return names 
    
    def _not(self, component):
        pass
    def _concatenate(self, ):
        pass
    def _gap(self, components):
        pass
    def _union(self, components):
        pass
    def _intersect(self, components):
        pass
    def _exact(self, contents, layer):
        pass
    def _regex(self, ignore_case=False):
        pass
    
    
query = {'op':'concat', 'components':[{'op':'match','terms':['tere','ilus'],'layer':'text','name':'asd','join_by':'union'}],'name':'asd2'}
ElasticGrammarQuery(query, None, None, None, None)


query = {'op':'union', 'name':'my_or', 'components':[{'op':'intersect', 'components':[{'op':'match','terms':['tere','ilus'],'layer':'text','name':'asd','join_by':'intersect'}],'name':'asd2'},{'op':'intersect', 'components':[{'op':'regex','expression':"tere\\d",'layer':'text','name':'asd4'}],'name':'asd3'}]}
ElasticGrammarQuery(query, None, None, None, None)