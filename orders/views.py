from django.shortcuts import render,get_object_or_404
from django.contrib.auth.decorators import login_required
from .models import Order,RefundRequest

# Create your views here.
@login_required
def orders_list(request):
    order = Order.objects.filter(user=request.user)
    context ={
       'orders':order 
    }
    return render(request,'orders_list.html',context)

def order_detail(request,order_id):
    order = get_object_or_404(Order,id=order_id,user=request.user)
    refunds = RefundRequest.objects.filter(order=order)
    context = {
        'order':order,
        'refunds':refunds,
    }
    return render(request,"order_detail.html",context)

