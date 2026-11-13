# Cookbook · Diagrama de atividades

Fluxo atual da interface web, da importação ao download do relatório.

```mermaid
flowchart TD
    start(["Início"]) --> entry{"Arquivo ou exemplo?"}

    subgraph reading ["Importação e leitura"]
        upload["Validar PDF, PNG ou JPEG"]
        gemini["Transcrever com Gemini"]
        luna["Transcrever com Luna"]
        compare["Comparar textos A e B com Luna"]
        extract["Extrair com Luna e validar evidências"]
        example["Carregar relatório do exemplo"]
        upload -->|"OCR em paralelo"| gemini
        upload -->|"OCR em paralelo"| luna
        gemini --> compare
        luna --> compare
        compare --> extract
    end

    entry -->|"Enviar arquivo"| upload
    entry -->|"Try example"| example

    subgraph review ["Review"]
        display["Exibir ingredientes, passos e alertas"]
        inspect["Consultar trecho e página de origem"]
        display -.->|"Selecionar item"| inspect
    end

    extract --> display
    example --> display

    subgraph adapt ["Adapt"]
        options["Definir porções e opção vegana"]
        valid{"Porções válidas, se exigidas?"}
        correct["Pedir porções ou correção"]
        requested{"Há ajuste solicitado?"}
        scale["Escalar medidas e passos, se solicitado"]
        vegan["Aplicar regras veganas, se solicitado"]
        changes["Exibir mudanças e alertas"]
        options --> valid
        valid -->|"Não"| correct
        correct --> options
        valid -->|"Sim"| requested
        requested -->|"Sim"| scale
        scale --> vegan
        vegan --> changes
        changes -.->|"Alterar opções"| options
    end

    display -->|"Abrir Adapt"| options
    inspect -.->|"Abrir Adapt"| options
    requested -->|"Não · relatório original"| download(["Exportar JSON"])
    changes -->|"Incluir adaptação"| download

    style reading fill:#E9F2FA,stroke:#8AA9C6,color:#1E1E1E
    style review fill:#F0F4FA,stroke:#9AAFC8,color:#1E1E1E
    style adapt fill:#E9F2FA,stroke:#8AA9C6,color:#1E1E1E
    style correct fill:#FFECBD,stroke:#D5A43A,color:#1E1E1E
```

- Em PDFs, Gemini recebe o arquivo original. Luna recebe as páginas rasterizadas, com fallback para PDF quando o documento excede o limite local. A comparação começa após as duas transcrições.
- Review permite consultar as evidências e seguir para Adapt com alertas pendentes. Um campo ligado a uma divergência fica sem valor e mantém a evidência para conferência.
- A escala exige uma contagem original confirmada. Adapt reaplica as regras em Python sobre a receita original a cada mudança. O JSON inclui a receita adaptada completa e os avisos que ainda exigem revisão.
