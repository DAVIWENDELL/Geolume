class InvalidInputError(Exception):
    """Entrada rejeitada; `codigo` é estável e pode ser exposto ao cliente."""

    def __init__(self, codigo: str, mensagem: str):
        super().__init__(f"{codigo}: {mensagem}")
        self.codigo = codigo
        self.mensagem = mensagem
