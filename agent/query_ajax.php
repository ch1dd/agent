<?php
require __DIR__ . '/auth.php';
require __DIR__ . '/db.php';
require_once __DIR__ . '/rag_client.php';

header('Content-Type: application/json; charset=utf-8');

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    http_response_code(405);
    echo json_encode(['success' => false, 'error' => 'Метод не разрешён']);
    exit;
}

verify_csrf();

$question = trim($_POST['question'] ?? '');
$user_id = $_SESSION['user_id'];

if (empty($question) || mb_strlen($question) > 1500) {
    echo json_encode(['success' => false, 'error' => 'Некорректный вопрос']);
    exit;
}

$owner = 'site:' . $user_id;
$chat = 'default';
$id = $_POST['request_id'] ?? '';
if (!preg_match('/^[0-9a-f]{32}$/D', $id)) {
    echo json_encode(['success' => false, 'error' => 'Некорректный request_id']);
    exit;
}

// Закрываем сессию до долгой операции
session_write_close();

try {
    $recent = $pdo->prepare("SELECT question,answer,human_answer,rag_result FROM queries
        WHERE user_id=? AND chat_id=? AND rag_result IS NOT NULL
          AND JSON_UNQUOTE(JSON_EXTRACT(rag_result, '$.kind')) <> 'off_topic'
        ORDER BY id DESC LIMIT 5");
    $recent->execute([$user_id, $chat]);
    $previous = [];
    foreach (array_reverse($recent->fetchAll()) as $r) {
        $meta = json_decode($r['rag_result'], true);
        $previous[] = [
            'question' => $r['question'],
            'text' => $r['answer'],
            'human_answer' => $r['human_answer'],
            'kind' => $meta['kind'] ?? 'answer'
        ];
    }
    
    $result = rag('/ask', [
        'owner' => $owner,
        'chat' => $chat,
        'request' => $id,
        'question' => $question,
        'previous' => $previous,
    ]);
    
    $sql = "INSERT INTO queries(user_id,chat_id,request_id,question,answer,rag_result,ticket_id)
        VALUES(?,?,?,?,?,?,?) 
        ON DUPLICATE KEY UPDATE request_id=VALUES(request_id)";
    $pdo->prepare($sql)->execute([
        $user_id, $chat, $id, $question, $result['text'],
        json_encode($result, JSON_THROW_ON_ERROR | JSON_UNESCAPED_UNICODE),
        $result['ticket_id'] ?? null
    ]);
    
    echo json_encode([
        'success' => true,
        'query' => [
            'id' => $pdo->lastInsertId(),
            'question' => $question,
            'answer' => $result['text'],
            'created_at' => date('Y-m-d H:i:s'),
        ]
    ], JSON_UNESCAPED_UNICODE);
    
} catch (Exception $e) {
    echo json_encode([
        'success' => false,
        'error' => $e->getMessage()
    ]);
}