<?php
require_once __DIR__ . '/rag_client.php';
require_once __DIR__ . '/answer_files.php';
require_once __DIR__ . '/withdrawals.php';

function ask_site_question(PDO $pdo, int $userId, string $question, string $id): array {
    if ($question === '' || mb_strlen($question) > 1500 || !preg_match('/^[0-9a-f]{32}$/D', $id)) {
        throw new InvalidArgumentException('Проверьте вопрос и идентификатор запроса.');
    }
    $chat = 'default';
    $recent = $pdo->prepare("SELECT q.request_id,q.question,q.answer,q.rag_result,COALESCE(t.admin_answer,q.human_answer) AS human_answer
        FROM queries q LEFT JOIN tickets t ON t.query_id=q.id AND t.id=q.ticket_id AND t.user_id=q.user_id
        WHERE q.user_id=? AND q.chat_id=? AND q.rag_result IS NOT NULL
          AND JSON_UNQUOTE(JSON_EXTRACT(q.rag_result, '$.kind')) NOT IN ('off_topic','withdrawn')
        ORDER BY q.id DESC LIMIT 5");
    $recent->execute([$userId, $chat]);
    $previous = [];
    foreach (array_reverse($recent->fetchAll()) as $r) {
        $meta = json_decode($r['rag_result'], true);
        $previous[] = ['question' => $r['question'], 'text' => $r['answer'], 'request' => $r['request_id'],
            'depends_on' => $meta['depends_on'] ?? array_column($meta['hits'] ?? [], 'version'),
            'human_answer' => isset($r['human_answer']) ? mb_substr($r['human_answer'], 0, 1500) : null,
            'kind' => $meta['kind'] ?? 'answer'];
    }
    $result = rag('/ask', ['owner' => 'site:' . $userId, 'chat' => $chat,
        'request' => $id, 'question' => $question, 'previous' => $previous]);
    $flags = JSON_THROW_ON_ERROR | JSON_UNESCAPED_UNICODE;
    $sql = "INSERT INTO queries(user_id,chat_id,request_id,question,answer,rag_result,files_json)
            VALUES (?,?,?,?,?,?,?) ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id),files_json=VALUES(files_json)";
    $files = answer_files($result);
    $pdo->prepare($sql)->execute([$userId, $chat, $id, $question, $result['text'],
        json_encode($result, $flags), json_encode($files, $flags)]);
    $queryId = (int)$pdo->lastInsertId();
    $removed = clearWithdrawnAnswers($pdo);
    if (isset($removed['site:' . $userId . '/' . $id])) {
        $result['text'] = 'Источник ответа удалён.';
        $files = [];
    }
    return ['id' => $queryId, 'question' => $question, 'answer' => $result['text'],
        'created_at' => date('Y-m-d H:i:s'), 'files' => $files];
}
