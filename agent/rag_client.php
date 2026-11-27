<?php
// Подключаем db.php, чтобы стали доступны константы RAG_API_TOKEN и RAG_REVIEW_TOKEN
require_once __DIR__ . '/db.php';

function rag(string $path, ?array $data = null, bool $admin = false): array {
    // Используем константы, которые уже определены в db.php
    $key = $admin ? RAG_REVIEW_TOKEN : RAG_API_TOKEN;
    
    $ch = curl_init('http://127.0.0.1:8765' . $path);
    $headers = ['Authorization: Bearer ' . $key];
    
    if ($data !== null) {
        $headers[] = 'Content-Type: application/json';
        curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode($data, JSON_THROW_ON_ERROR | JSON_UNESCAPED_UNICODE));
    }
    
    curl_setopt_array($ch, [
        CURLOPT_HTTPHEADER => $headers, 
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_CONNECTTIMEOUT => 5, 
        CURLOPT_TIMEOUT => 1200
    ]);
    
    $raw = curl_exec($ch);
    $code = curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
    
    if ($raw === false || $code < 200 || $code >= 300) {
        throw new RuntimeException('Сервис консультанта недоступен, HTTP ' . $code . ': ' . $raw);
    }
    
    return json_decode($raw, true, 512, JSON_THROW_ON_ERROR);
}