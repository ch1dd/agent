<?php
require __DIR__ . '/auth.php';
require __DIR__ . '/db.php';

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    http_response_code(405);
    exit;
}
verify_csrf();
require_once __DIR__ . '/withdrawals.php';
clearWithdrawnAnswers($pdo);

$query_id = (int)($_POST['query_id'] ?? 0);
$user_id = $_SESSION['user_id'];

try {
    $pdo->beginTransaction();
    $stmt = $pdo->prepare("SELECT q.question,q.rag_result,t.id AS existing_ticket FROM queries q
        LEFT JOIN tickets t ON t.id=q.ticket_id AND t.query_id=q.id AND t.user_id=q.user_id
        WHERE q.id=? AND q.user_id=? FOR UPDATE");
    $stmt->execute([$query_id, $user_id]);
    $row = $stmt->fetch();
    if (!$row || !in_array(json_decode($row['rag_result'] ?? '', true)['kind'] ?? '', ['unknown','off_topic'], true)) {
        throw new RuntimeException('Вопрос для заявки не найден.');
    }
    if ($row['existing_ticket']) {
        $pdo->commit();
        header('Location: dashboard.php');
        exit;
    }

    $stmt = $pdo->prepare("INSERT INTO tickets (user_id, query_id, question, status) VALUES (?, ?, ?, 'open')");
    $stmt->execute([$user_id, $query_id, $row['question']]);
    $ticket_id = $pdo->lastInsertId();
    
    // 2. Привязываем ID заявки к истории чата
    $stmt = $pdo->prepare("UPDATE queries SET ticket_id = ? WHERE id = ?");
    $stmt->execute([$ticket_id, $query_id]);
    
    $pdo->commit();
} catch (Exception $e) {
    $pdo->rollBack();
    $_SESSION['ticket_error'] = 'Ошибка создания заявки: ' . $e->getMessage();
}

header('Location: dashboard.php');
exit;
