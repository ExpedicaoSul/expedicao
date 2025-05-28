import sqlite3
from flask import Flask, render_template, request, jsonify, g, redirect, url_for
import pandas as pd
import os
import re
import traceback
import json
from datetime import datetime

# Inicialização do Flask
app = Flask(__name__)
# Usamos app.instance_path para garantir que o DB esteja em um local gravável e isolado
app.config['DATABASE'] = os.path.join(app.instance_path, 'site.db')

# Garante que a pasta instance_path existe para o banco de dados
os.makedirs(app.instance_path, exist_ok=True)

# Função para obter a conexão com o banco de dados
# Usa 'g' (objeto global do Flask) para armazenar a conexão e reutilizá-la por requisição
def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(
            app.config['DATABASE'],
            detect_types=sqlite3.PARSE_DECLTYPES
        )
        g.db.row_factory = sqlite3.Row # Permite acessar colunas por nome
    return g.db

# Função para fechar a conexão com o banco de dados ao final de cada requisição
@app.teardown_appcontext
def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()

# Função para criar a tabela 'pedidos' (dados do Excel)
# Esta é a função que o comando 'flask initdb' vai chamar
def init_app_db():
    db = get_db() # Usa a conexão gerida pelo Flask
    with app.open_resource('schema.sql', mode='r') as f:
        db.cursor().executescript(f.read())
    db.commit()
    # Não fechar db.close() aqui, pois get_db() e close_db() gerenciam

# Função para criar a tabela 'expedicoes' (dados do formulário)
def init_expedicoes_db():
    with app.app_context():
        db = get_db()
        cursor = db.cursor()
        
        # Cria a tabela expedicoes se ela não existir com todas as colunas atuais
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS expedicoes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pedido_numero TEXT NOT NULL,
                linha_sola TEXT,
                diaria TEXT,
                cores_selecionadas TEXT,
                transportadora TEXT,
                local TEXT NOT NULL,
                observacao TEXT,
                quantidade INTEGER NOT NULL,
                data_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                -- A coluna 'nf' será adicionada pela lógica de migração abaixo se não existir
            )
        ''')
        
        # --- COLE ESTE BLOCO ABAIXO (MIGRAÇÃO PARA ADICIONAR A COLUNA 'NF') ---
        cursor.execute("PRAGMA table_info(expedicoes)")
        columns = [column[1] for column in cursor.fetchall()]
        if 'nf' not in columns:
            print("Adicionando coluna 'nf' à tabela expedicoes...")
            cursor.execute("ALTER TABLE expedicoes ADD COLUMN nf TEXT DEFAULT ''")
            db.commit() # Commit após a alteração da tabela
            print("Coluna 'nf' adicionada com sucesso.")
        # --- FIM DO BLOCO DE MIGRAÇÃO ---

        db.commit() # Commit da criação da tabela, se for o caso
    # Não fechar conn.close() aqui, pois get_db() e close_db() gerenciam

# Chamada para criar a tabela 'expedicoes' ao iniciar o app
# Isso será executado uma vez, quando o app for iniciado.
with app.app_context():
    init_expedicoes_db()

# Comando CLI para inicializar o banco de dados principal (pedidos)
@app.cli.command('initdb')
def initdb_command():
    """Inicialize o banco de dados 'pedidos' do esquema."""
    init_app_db() # Chama a nova função para criar a tabela 'pedidos'
    print('Inicializado o banco de dados de pedidos.')

# Rota para upload de arquivo Excel
@app.route('/', methods=['GET', 'POST'])
def upload_file():
    if request.method == 'POST':
        if 'file' not in request.files:
            return jsonify({'success': False, 'message': 'Nenhum arquivo enviado.'}), 400
        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'message': 'Nenhum arquivo selecionado.'}), 400
        if file and file.filename.endswith('.xlsx'):
            try:
                df = pd.read_excel(file, header=5) # Lendo a partir da linha 6 (índice 5)

                print("Nomes das colunas após a leitura do arquivo:")
                print(df.columns)

                save_data_to_db(df)
                
                # Redireciona para o formulário de expedição após o sucesso do upload
                return redirect(url_for('expedition_form'))

            except Exception as e:
                error_message = f'Erro ao processar o arquivo: {e}\n\nTraceback:\n{traceback.format_exc()}'
                print(error_message)
                return jsonify({'success': False, 'message': error_message}), 500
        else:
            return jsonify({'success': False, 'message': 'Por favor, envie um arquivo .xlsx.'}), 400
    
    # Se o método for GET, renderiza o template de upload
    return render_template('upload.html')

# Função para salvar os dados do DataFrame na tabela 'pedidos'
def save_data_to_db(df):
    db = get_db() # Usa a conexão gerida pelo Flask
    cursor = db.cursor()
    try:
        # A criação da tabela 'pedidos' é feita por init_app_db() via 'flask initdb'
        # ou se o 'schema.sql' for executado em outro lugar.
        # Por segurança, você pode ter um CREATE TABLE IF NOT EXISTS aqui também,
        # mas o ideal é que seja gerido pelo schema.sql
        
        # Limpar a tabela 'pedidos' antes de inserir novos dados
        cursor.execute("DELETE FROM pedidos")

        # Limpar a tabela 'expedicoes' também, pois os dados dos pedidos foram resetados
        cursor.execute("DELETE FROM expedicoes")

        # Inserir os dados do DataFrame na tabela, usando os nomes exatos das colunas
        for index, row in df.iterrows():
            data_entrega = pd.to_datetime(row['Dt.entr.item']).strftime('%Y-%m-%d %H:%M:%S') if pd.notna(row['Dt.entr.item']) else None
            cursor.execute('''
                INSERT INTO pedidos (Dt_entr_item, Pedido, Ordem_com, Remessa, Razao_social, Produto, Desc_completa, Ref_item_ped, Grupo, Descricao, Qtd_item)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (data_entrega, row['Pedido'], row['Ordem com'], row['Remessa'], row['Razão social'], row['Produto'], row['Desc.completa'], row['Ref.item ped'], row['Grupo'], row['Descrição'], row['Qtd.item']))

        db.commit()
    except sqlite3.Error as e:
        print(f"Erro de banco de dados ao salvar dados do Excel: {e}")
        # Re-lança a exceção para ser capturada na rota de upload
        raise
    except KeyError as e:
        print(f"Erro de coluna ausente no Excel: {e}")
        raise ValueError(f"Uma coluna esperada não foi encontrada no arquivo Excel: {e}")
    # Não fechar db.close() aqui, pois get_db() e close_db() gerenciam

# Rota para o formulário de expedição
@app.route('/expedicao', methods=['GET'])
def expedition_form():
    return render_template('expedition_form.html')

# Rota para a página de entrada manual
@app.route('/entrada_manual')
def manual_entry_form():
    return render_template('entrada_manual.html')

# Rota para buscar detalhes do pedido
@app.route('/buscar_pedido/<pedido_numero>')
def buscar_pedido(pedido_numero):
    db = get_db()
    cursor = db.cursor()

    cursor.execute("SELECT Ref_item_ped, Dt_entr_item, Desc_completa FROM pedidos WHERE Pedido = ?", (pedido_numero,))
    resultados_pedidos = cursor.fetchall()

    linha = ''
    diaria_formatada = ''
    
    cores_expedidas_originais = set() 
    cursor.execute("SELECT cores_selecionadas FROM expedicoes WHERE pedido_numero = ?", (pedido_numero,))
    resultados_expedicoes = cursor.fetchall()
    
    print(f"\n--- DEBUG (buscar_pedido): Cores Expedidas Atualmente para o Pedido {pedido_numero} ---")
    for row in resultados_expedicoes:
        try:
            expedidas_list = json.loads(row['cores_selecionadas'])
            for cor_expedida in expedidas_list:
                cores_expedidas_originais.add(cor_expedida) 
                print(f"  - Expedida (Original): '{cor_expedida}'")
        except json.JSONDecodeError as e:
            print(f"  - ERRO: Falha ao decodificar JSON de cores_selecionadas: {e} para '{row['cores_selecionadas']}'")
            continue

    cores_disponiveis_para_frontend = [] 

    if resultados_pedidos:
        linha = resultados_pedidos[0]['Ref_item_ped']
        data_diaria_str = resultados_pedidos[0]['Dt_entr_item']

        if data_diaria_str:
            try:
                data_part = data_diaria_str.split(' ')[0]
                data_diaria = datetime.strptime(data_part, '%Y-%m-%d')
                diaria_formatada = data_diaria.strftime('%d/%m')
            except ValueError:
                diaria_formatada = data_diaria_str

        print(f"\n--- DEBUG (buscar_pedido): Processando Descrições Completas para o Pedido {pedido_numero} ---")
        for resultado_row in resultados_pedidos:
            desc_completa_original = resultado_row['Desc_completa'].strip() 
            print(f"  - Descrição Completa Original: '{desc_completa_original}'")
            
            cor_formatada_para_exibir = desc_completa_original 
            
            cor_start_index = desc_completa_original.upper().find('COR')

            if cor_start_index != -1:
                sub_string_apos_cor = desc_completa_original[cor_start_index:].strip()

                if '/' in sub_string_apos_cor:
                    cor_formatada_para_exibir = sub_string_apos_cor
                    print(f"  - Padrão 'COR.../' detectado. Formatado: '{cor_formatada_para_exibir}'")
                else:
                    # NOVA REGEX PARA CORES SEM BARRA:
                    # Captura "COR" + espaços, opcionalmente um número + espaços,
                    # e então captura o restante da linha até um limite ou fim da string.
                    # O `(.*)` é mais ganancioso para pegar tudo até o final,
                    # e depois cortamos o que não é parte da cor.
                    match_simples = re.search(r'COR\s+(?:\d+\s+)?(.*)', desc_completa_original, re.IGNORECASE)

                    if match_simples:
                        cor_capturada_raw = match_simples.group(1).strip()
                        
                        # Tenta limpar o que vem depois do nome da cor (ex: "DIÁRIA", "SOLA")
                        # e remover números de cores se ainda estiverem no começo.
                        # Ex: "0050 NATURAL" -> "NATURAL"
                        # Ex: "BRANCO 99 DIÁRIA" -> "BRANCO 99"
                        
                        # Primeiro, limpa o número da cor se ele está no início da string capturada (ex: "0050 NATURAL")
                        cor_capturada_limpa = re.sub(r'^\d+\s+', '', cor_capturada_raw).strip()

                        # Remove palavras como "DIÁRIA", "SOLA", ou outros termos comuns do final
                        cor_capturada_limpa = re.sub(r'\s+(DIÁRIA|SOLA|ITEM|PEDIDO)\s*$', '', cor_capturada_limpa, flags=re.IGNORECASE).strip()
                        
                        # Se a limpeza resultar em algo vazio, volte para a raw, ou decida o que é melhor.
                        # Para "NATURAL" e "PRETO", o `^\d+\s+` vai resolver.
                        
                        cor_formatada_para_exibir = cor_capturada_limpa

                        print(f"  - Padrão 'COR' sem barra detectado. Formatado: '{cor_formatada_para_exibir}'")
                    else:
                        cor_formatada_para_exibir = sub_string_apos_cor 
                        print(f"  - Padrão 'COR' sem barra, mas regex simples falhou. Fallback: '{cor_formatada_para_exibir}'")
            else:
                print(f"  - 'COR' não encontrado na string. Usando original: '{cor_formatada_para_exibir}'")

            if desc_completa_original not in cores_expedidas_originais:
                cores_disponiveis_para_frontend.append({
                    'value': desc_completa_original, 
                    'text': cor_formatada_para_exibir 
                })
                print(f"  - Cor DISPONÍVEL: Value='{desc_completa_original}', Text='{cor_formatada_para_exibir}'")
            else:
                print(f"  - Cor JÁ EXPEDIDA (Original): '{desc_completa_original}'. Ignorando.")

    print(f"\n--- DEBUG (buscar_pedido): Cores Finais Enviadas ao Frontend para {pedido_numero} ---")
    final_unique_cores = {}
    for item in cores_disponiveis_para_frontend:
        final_unique_cores[item['value']] = item 

    sorted_cores = sorted(final_unique_cores.values(), key=lambda x: x['text'])
    print(sorted_cores)
    
    return jsonify({'linha': linha, 'diaria': diaria_formatada, 'cores': sorted_cores})

# Rota para calcular a quantidade
@app.route('/calcular_quantidade/<pedido_numero>', methods=['POST'])
def calcular_quantidade(pedido_numero):
    data = request.get_json()
    cores_selecionadas_do_frontend = data.get('cores', []) # Estas são as strings originais agora!

    print(f"\n--- DEBUG (calcular_quantidade): Chamada para pedido: {pedido_numero}")
    print(f"  - Cores selecionadas do frontend (valores originais): {cores_selecionadas_do_frontend}")

    if not cores_selecionadas_do_frontend:
        print("  - Nenhuma cor selecionada, retornando 0.")
        return jsonify({'quantidade': 0})

    db = get_db()
    cursor = db.cursor()

    quantidade_total = 0
    
    cursor.execute("SELECT Desc_completa, Qtd_item FROM pedidos WHERE Pedido = ?", (pedido_numero,))
    resultados_excel = cursor.fetchall()

    if not resultados_excel:
        print(f"  - Nenhum resultado encontrado na tabela 'pedidos' para o pedido {pedido_numero}.")
        return jsonify({'quantidade': 0})

    # Aqui a comparação é direta: se a 'Desc_completa' do Excel está entre as 'cores_selecionadas_do_frontend'
    for resultado_row in resultados_excel:
        desc_completa_excel = resultado_row['Desc_completa']
        qtd_item = resultado_row['Qtd_item']
        
        print(f"  - Verificando Desc_completa do Excel: '{desc_completa_excel}'")
        
        if desc_completa_excel in cores_selecionadas_do_frontend:
            quantidade_total += qtd_item
            print(f"  - Correspondência exata encontrada para '{desc_completa_excel}'. Adicionando Qtd: {qtd_item}")
        else:
            print(f"  - Sem correspondência para '{desc_completa_excel}' nas cores selecionadas.")

    print(f"--- DEBUG (calcular_quantidade): Quantidade total calculada: {quantidade_total}")
    return jsonify({'quantidade': quantidade_total})

# Rota para salvar os dados da expedição
@app.route('/salvar_expedicao', methods=['POST'])
def salvar_expedicao():
    data = request.get_json()
    
    # ### ALTERADO: pedido_numero agora aceita None para entradas manuais
    pedido_numero = data.get('pedido_numero') 
    linha_sola = data.get('linha_sola')
    diaria = data.get('diaria')
    cores_selecionadas_originais = data.get('cores_selecionadas', []) # Estas são as strings originais!
    transportadora = data.get('transportadora')
    local = data.get('local')
    observacao = data.get('observacao')
    quantidade = data.get('quantidade')
    nf = data.get('nf')

    # ### NOVO: Validação para campos obrigatórios (incluindo o pedido_numero para formulários não-manuais)
    # Para o formulário manual, pedido_numero será 'MANUAL' (string) ou None.
    # A verificação 'not pedido_numero' funcionará para None ou string vazia,
    # mas 'MANUAL' será tratado como preenchido.
    if not linha_sola or not diaria or not cores_selecionadas_originais or not transportadora or not local or not quantidade or not nf:
        return jsonify({'success': False, 'message': 'Por favor, preencha todos os campos obrigatórios (Linha/Sola, Diária, Cores, Transportadora, Local, Quantidade, NF).'}), 400

    # Convertendo a lista de cores selecionadas para uma string JSON
    cores_selecionadas_json_str = json.dumps(cores_selecionadas_originais) 

    print(f"\n--- DEBUG (salvar_expedicao): Salvando Expedição ---")
    print(f"  - Pedido: {pedido_numero}, NF: {nf}")
    print(f"  - Cores selecionadas (Originais para salvar): {cores_selecionadas_originais}")
    print(f"  - JSON para DB: '{cores_selecionadas_json_str}'")

    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute('''
            INSERT INTO expedicoes (
                pedido_numero, linha_sola, diaria, cores_selecionadas,
                transportadora, local, observacao, quantidade, nf, data_registro
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ''', (
            pedido_numero, # Pode ser None ou 'MANUAL'
            linha_sola, 
            diaria, 
            cores_selecionadas_json_str, # Salva o JSON da lista original
            transportadora, 
            local, 
            observacao, 
            quantidade, 
            nf
        ))
        db.commit()
        print(f"  - Expedição salva com sucesso no DB.")
        return jsonify({'success': True, 'message': 'Pedido de expedição adicionado com sucesso!'})
    except sqlite3.Error as e:
        db.rollback()
        print(f"  - ERRO ao salvar expedição: {str(e)}")
        return jsonify({'success': False, 'message': f'Erro ao salvar no banco de dados: {str(e)}'}), 500

@app.route('/relatorio_detalhado')
def relatorio_detalhado():
    return render_template('relatorio_detalhado.html')

@app.route('/api/relatorio_detalhado_dados')
def get_relatorio_detalhado_dados():
    db = get_db()
    cursor = db.cursor()
    # Garante que id e pedido_numero são selecionados para uso no frontend (botão excluir)
    cursor.execute("SELECT id, pedido_numero, nf, linha_sola, diaria, cores_selecionadas, transportadora, local, observacao, quantidade, data_registro FROM expedicoes ORDER BY data_registro DESC")
    expedicoes = cursor.fetchall()

    expedicoes_formatadas = [] 

    for expedicao in expedicoes:
        item = dict(expedicao) # Converte sqlite3.Row para um dicionário mutável
        
        cores_originais_json = item.get('cores_selecionadas', '[]')
        
        try:
            cores_list_from_db = json.loads(cores_originais_json)
            # Garante que cores_list_from_db é uma lista, mesmo que o JSON seja uma string simples
            if not isinstance(cores_list_from_db, list):
                cores_list_from_db = [cores_list_from_db]
        except json.JSONDecodeError:
            # Em caso de erro de decodificação, trata a string bruta como uma única cor
            cores_list_from_db = [cores_originais_json] 
            print(f"ATENÇÃO: 'cores_selecionadas' não é JSON válido para registro ID {item.get('id')}. Conteúdo: {cores_originais_json}")

        cores_formatadas_para_exibir_lista = [] 

        for desc_completa_original in cores_list_from_db: # Itera sobre a lista (pode ser de 1 item)
            desc_completa_original = str(desc_completa_original).strip() # Garante que é string
            
            cor_formatada_para_exibir_singular = desc_completa_original # Valor padrão
            
            cor_start_index = desc_completa_original.upper().find('COR')

            if cor_start_index != -1:
                sub_string_apos_cor = desc_completa_original[cor_start_index:].strip()

                if '/' in sub_string_apos_cor:
                    cor_formatada_para_exibir_singular = sub_string_apos_cor
                else:
                    match_simples = re.search(r'COR\s+(?:\d+\s+)?(.*)', desc_completa_original, re.IGNORECASE)

                    if match_simples:
                        cor_capturada_raw = match_simples.group(1).strip()
                        cor_capturada_limpa = re.sub(r'^\d+\s+', '', cor_capturada_raw).strip()
                        cor_capturada_limpa = re.sub(r'\s+(DIÁRIA|SOLA|ITEM|PEDIDO)\s*$', '', cor_capturada_limpa, flags=re.IGNORECASE).strip()
                        
                        cor_formatada_para_exibir_singular = cor_capturada_limpa
                    else:
                        cor_formatada_para_exibir_singular = sub_string_apos_cor
            else:
                cor_formatada_para_exibir_singular = desc_completa_original

            cores_formatadas_para_exibir_lista.append(cor_formatada_para_exibir_singular)
        
        # ATENÇÃO: AQUI MODIFICAMOS A CHAVE EXISTENTE 'cores_selecionadas' para conter a lista formatada
        item['cores_selecionadas'] = cores_formatadas_para_exibir_lista 
        
        expedicoes_formatadas.append(item) 

    return jsonify(expedicoes_formatadas)

@app.route('/relatorio_agrupado')
def relatorio_agrupado():
    return render_template('relatorio_agrupado.html')

@app.route('/api/relatorio_agrupado_dados')
def get_relatorio_agrupado_dados():
    db = get_db()
    cursor = db.cursor()

    # Agrupa por linha_sola
    cursor.execute("""
        SELECT linha_sola, SUM(quantidade) AS total_quantidade
        FROM expedicoes
        WHERE linha_sola IS NOT NULL AND linha_sola != '' -- Ignora linhas vazias ou nulas
        GROUP BY linha_sola
        ORDER BY linha_sola
    """)

    dados_agrupados = cursor.fetchall()

    # --- Calcular o total geral ---
    cursor.execute("SELECT SUM(quantidade) AS total_geral FROM expedicoes")
    total_geral_row = cursor.fetchone()
    total_geral = total_geral_row['total_geral'] if total_geral_row and total_geral_row['total_geral'] is not None else 0

    # Retorna tanto os dados agrupados quanto o total geral
    return jsonify({
        'dados_agrupados': [dict(row) for row in dados_agrupados],
        'total_geral': total_geral
    })

@app.route('/api/excluir_expedicao/<int:expedicao_id>', methods=['DELETE'])
def excluir_expedicao(expedicao_id):
    db = get_db()
    cursor = db.cursor()
    
    # 1. Recuperar informações da expedição ANTES de excluir (especialmente cores_selecionadas e pedido_numero)
    try:
        cursor.execute("SELECT pedido_numero, cores_selecionadas FROM expedicoes WHERE id = ?", (expedicao_id,))
        expedicao_para_excluir = cursor.fetchone()
        
        if not expedicao_para_excluir:
            return jsonify({'success': False, 'message': 'Expedição não encontrada.'}), 404
            
        pedido_numero_excluido = expedicao_para_excluir['pedido_numero']
        cores_selecionadas_excluidas_json = expedicao_para_excluir['cores_selecionadas']
        
        # 2. Excluir o registro da expedição
        cursor.execute("DELETE FROM expedicoes WHERE id = ?", (expedicao_id,))
        db.commit() # Commit da exclusão
        
        # A lógica para reabilitar a cor no formulário é implícita
        # O formulário de expedição (rota /buscar_pedido/<pedido_numero>)
        # já consulta as cores JÁ expedidas para o pedido.
        # Ao remover um registro de expedição, essa cor deixa de ser "expedida"
        # para aquele pedido, e portanto, voltará a ser exibida como disponível
        # quando o pedido for buscado novamente no formulário.
        # Não é necessário modificar diretamente a tabela 'pedidos' ou qualquer outra.
        
        return jsonify({'success': True, 'message': f'Expedição {expedicao_id} excluída com sucesso! Cores restauradas para o pedido {pedido_numero_excluido}.'})

    except sqlite3.Error as e:
        db.rollback() # Em caso de erro, desfaz a transação
        return jsonify({'success': False, 'message': f'Erro ao excluir expedição: {str(e)}'}), 500
    except json.JSONDecodeError:
        db.rollback()
        return jsonify({'success': False, 'message': 'Erro ao processar dados de cores da expedição.'}), 500

if __name__ == '__main__':
    app.run(debug=True)