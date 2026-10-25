"""Company-wide chrome with an explicit first-product boundary."""
def company_navigation(request):
    return {'product_scope': request.path.startswith('/products/facerecognition/'),
            'company_name': 'NxFi Technologies'}
