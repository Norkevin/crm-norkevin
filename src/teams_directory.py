"""Local shared people directory; private identity/payment data never enters member APIs."""
DIRECTORY = '_teams_directory'
PRIVATE_FIELDS = {'bank':'Banco', 'account_type':'Tipo de cuenta', 'account_number':'Número de cuenta',
                  'account_holder':'Titular de la cuenta',
                  'dpi':'DPI', 'plates':'Placas', 'notes':'Notas internas'}
