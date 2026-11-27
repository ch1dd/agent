<?php
require __DIR__ . '/auth.php';
requireAdmin();
// Подключение rag_client.php автоматически подтянет db.php и константы!
require_once __DIR__ . '/rag_client.php';

if ($_SERVER['REQUEST_METHOD'] !== 'POST') { 
    http_response_code(405); 
    exit('Метод не разрешён'); 
}

verify_csrf();

$f = $_FILES['document'] ?? null;
if (!$f || $f['error'] !== UPLOAD_ERR_OK || !is_uploaded_file($f['tmp_name']) || $f['size'] > 64 * 1024 * 1024) {
    throw new RuntimeException('Ошибка загрузки файла');
}

$ch = curl_init('http://127.0.0.1:8765/admin/uploads?name=' . rawurlencode(basename($f['name'])));
$fp = fopen($f['tmp_name'], 'rb');
try {
    curl_setopt_array($ch, [
        CURLOPT_UPLOAD => true, 
        CURLOPT_CUSTOMREQUEST => 'POST', 
        CURLOPT_INFILE => $fp,
        CURLOPT_INFILESIZE => $f['size'], 
        CURLOPT_RETURNTRANSFER => true, 
        CURLOPT_TIMEOUT => 120,
        CURLOPT_HTTPHEADER => [
            'Content-Type: application/octet-stream',
            // Используем константу, которая пришла из db.php через rag_client.php
            'Authorization: Bearer ' . RAG_REVIEW_TOKEN
        ]
    ]);
    
    $raw = curl_exec($ch);
    $code = curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
    
    if ($raw === false || $code !== 202) {
        throw new RuntimeException('Сервис не принял файл, HTTP ' . $code . ': ' . $raw);
    }
    
    $job = json_decode($raw, true, 512, JSON_THROW_ON_ERROR)['job_id'];
} finally {
    fclose($fp);
}

echo '<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>Результат загрузки</title>
    <link rel="stylesheet" href="styles.css">
</head>
<body>
    <header>
        <a href="index.php">AI Agent</a>
        <nav>
            <a href="admin.php">Админка</a>
            <a href="logout.php?token=' . csrf_token() . '">Выйти</a>
        </nav>
    </header>
    <main>
        <div class="card">
            <h1>Документ загружен</h1>
            <p style="margin-top: 16px;">Номер задания: <strong>' . htmlspecialchars($job, ENT_QUOTES, 'UTF-8') . '</strong></p>
            <p style="margin-top: 16px;">
                <a href="admin.php" class="btn btn-primary">Вернуться в админку</a>
            </p>
        </div>
    </main>
</body>
</html>';