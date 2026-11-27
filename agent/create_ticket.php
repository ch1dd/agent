<?php
require __DIR__ . '/auth.php';
require __DIR__ . '/db.php';

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    http_response_code(405);
    exit;
}
verify_csrf();

$query_id = (int)($_POST['query_id'] ?? 0);
$user_id = $_SESSION['user_id'];

// Получаем текст вопроса
$stmt = $pdo->prepare("SELECT question FROM queries WHERE id = ? AND user_id = ?");
$stmt->execute([$query_id, $user_id]);
$row = $stmt->fetch();

if (!$row) {
    http_response_code(404);
    exit('Запрос не найден');
}

try {
    $pdo->beginTransaction();
    
    // 1. Создаём заявку в таблице tickets
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