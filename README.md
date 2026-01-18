# Bancos Reader – Extractor Inteligente de Estados de Cuenta

Sistema desarrollado en Python para la lectura y procesamiento automático de estados de cuenta bancarios en formato PDF, transformándolos en archivos Excel estructurados listos para análisis financiero.

Este proyecto fue creado como un **boceto/prototipo funcional para la empresa Tecnología Empresarial**, con el objetivo de evaluar la viabilidad de automatizar la lectura masiva de estados de cuenta bancarios y su integración a procesos contables.

El sistema está diseñado bajo una arquitectura modular que permite adaptar distintos formatos por banco y tipo de producto (débito y crédito).

---

## Objetivo
Automatizar la extracción de movimientos bancarios desde PDFs para reducir el trabajo manual de captura y generar información organizada para análisis contable y financiero.

---

## Funcionalidades
- Lectura automática de PDFs bancarios  
- Identificación del banco y tipo de producto  
- Extracción estructurada de movimientos  
- Normalización de columnas financieras  
- Generación automática de Excel  
- Soporte para múltiples formatos de estado de cuenta  
- Arquitectura escalable para integrar nuevos bancos  
- Preparado para OCR en PDFs escaneados  

---

## Enfoque técnico
El sistema implementa un pipeline de procesamiento:

1. Detección automática del banco por contenido del PDF  
2. Selección dinámica del parser correspondiente  
3. Extracción de texto y detección de columnas  
4. Construcción estructurada de DataFrames  
5. Exportación automática a Excel  

Cada banco tiene su propio parser independiente para facilitar mantenimiento y escalabilidad.

---

## Tecnologías
- Python  
- Pandas  
- PDFPlumber  
- OpenPyXL  
- Tkinter (versión escritorio)  

---

## Estructura del proyecto
```
bancos-reader/
├── parsers/
├── core/
├── ui/
├── templates/
├── outputs/
└── main.py
```


---

## Salida generada
- Excel estructurado por banco  
- Movimientos normalizados  
- Clasificación por tipo de operación  

---

## Escalabilidad
- Integración de nuevos bancos  
- Adaptación a nuevos formatos  
- Lectura OCR para PDFs escaneados  
- Migración a entorno web/API  

---

Sistema desarrollado como prototipo de automatización financiera aplicado a procesamiento inteligente de documentos bancarios para Tecnología Empresarial.
