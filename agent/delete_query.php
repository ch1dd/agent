<?php
require 'auth.php';
require 'db.php';

if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['query_id'])) {
    verify_csrf();
    $query_id = (int)$_POST['query_id'];
    $user_id = $_SESSION['user_id'];
    
    // Удаляем ТОЛЬКО свой запрос (защита от удаления чужих)
    try {
        $stmt = $pdo->prepare("DELETE FROM queries WHERE id = ? AND user_id = ?");
        $stmt->execute([$query_id, $user_id]);
    } catch (PDOException $e) {
        die("Ошибка удаления: " . $e->getMessage());
    }
}

header('Location: dashboard.php');
exit;