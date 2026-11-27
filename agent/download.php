<?php
require __DIR__ . '/auth.php';
require __DIR__ . '/db.php';
require_once __DIR__ . '/rag_client.php';

$query_id = (int)($_GET['query_id'] ?? 0);
$version = (int)($_GET['version'] ?? 0);

if ($query_id <= 0 || $version <= 0) {
    http_response_code(400);
    exit('Неверные параметры');
}

$stmt = $pdo->prepare("SELECT rag_result FROM queries WHERE id = ? AND user_id = ?");
$stmt->execute([$query_id, $_SESSION['user_id']]);
$row = $stmt->fetch();

if (!$row) {
    http_response_code(404);
    exit('Запрос не найден');
}

$rag = json_decode($row['rag_result'], true);
$hits = $rag['hits'] ?? [];

$found = null;
foreach ($hits as $hit) {
    if (($hit['version'] ?? null) === $version) {
        $found = $hit;
        break;
    }
}

if (!$found) {
    http_response_code(404);
    exit('Источник не найден');
}

try {
    $ch = curl_init('http://127.0.0.1:8765/originals/' . $version);
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_HTTPHEADER => ['Authorization: Bearer ' . RAG_API_TOKEN],
        CURLOPT_TIMEOUT => 60,
        CURLOPT_HEADER => true,
    ]);
    
    $response = curl_exec($ch);
    $code = curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
    $headerSize = curl_getinfo($ch, CURLINFO_HEADER_SIZE);
    curl_close($ch);
    
    if ($code !== 200) {
        http_response_code(404);
        exit('Файл недоступен');
    }
    
    $headers = substr($response, 0, $headerSize);
    $body = substr($response, $headerSize);
    
    $contentType = 'application/octet-stream';
    $filename = 'document';
    if (preg_match('/Content-Type:\s*(.+)/i', $headers, $m)) {
        $contentType = trim($m[1]);
    }
    if (preg_match('/Content-Disposition:.*filename="?([^";\n]+)"?/i', $headers, $m)) {
        $filename = trim($m[1]);
    }
    
    header('Content-Type: ' . $contentType);
    header('Content-Disposition: attachment; filename="' . basename($filename) . '"');
    header('Content-Length: ' . strlen($body));
    echo $body;
    exit;
} catch (Exception $e) {
    http_response_code(500);
    exit('Ошибка скачивания');
}