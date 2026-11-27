<?php
require __DIR__ . '/auth.php';
require __DIR__ . '/db.php';
require_once __DIR__ . '/rag_client.php';

$query_id = (int)($_GET['query_id'] ?? 0);
$version = $_GET['version'] ?? '';

if ($query_id <= 0 || !is_string($version) || !preg_match('/^[a-f0-9]{64}$/D', $version)) {
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
foreach ($rag['used'] ?? [] as $index) {
    $hit = $hits[$index] ?? [];
    if (($hit['version'] ?? null) === $version) {
        $found = $hit;
        break;
    }
}

if (!$found) {
    http_response_code(404);
    exit('Источник не найден');
}

session_write_close();
$file = tmpfile();
if ($file === false) {
    http_response_code(500);
    exit('Не удалось подготовить файл');
}

$ch = curl_init('http://127.0.0.1:8765/originals/' . $version);
$disposition = "attachment; filename*=UTF-8''" . rawurlencode(basename($found['passage']['source']));
curl_setopt_array($ch, [
    CURLOPT_FILE => $file,
    CURLOPT_HTTPHEADER => ['Authorization: Bearer ' . RAG_API_TOKEN],
    CURLOPT_TIMEOUT => 60,
    CURLOPT_HEADERFUNCTION => function ($ch, $line) use (&$disposition) {
        if (preg_match('/^Content-Disposition:\s*(attachment[^\r\n]*)/i', $line, $m)) {
            $disposition = trim($m[1]);
        }
        return strlen($line);
    },
]);
$ok = curl_exec($ch);
$code = curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
curl_close($ch);
if (!$ok || $code !== 200) {
    fclose($file);
    http_response_code(404);
    exit('Файл недоступен');
}

rewind($file);
header('Content-Type: application/octet-stream');
header('Content-Disposition: ' . $disposition);
header('Cache-Control: private, no-store');
header('Content-Length: ' . fstat($file)['size']);
fpassthru($file);
fclose($file);
